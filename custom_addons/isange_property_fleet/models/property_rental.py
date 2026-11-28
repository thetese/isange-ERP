# -*- coding: utf-8 -*-
from dateutil.relativedelta import relativedelta
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class IsangeProperty(models.Model):
    _name = 'isange.property'
    _description = 'Rental Property / House'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name asc'

    name = fields.Char(string='House / Property Name', required=True, tracking=True)
    code = fields.Char(string='House Code', tracking=True)
    active = fields.Boolean(default=True)
    property_type = fields.Selection([
        ('house', 'Residential House'),
        ('apartment', 'Apartment / Unit'),
        ('commercial', 'Commercial Shop / Office'),
        ('warehouse', 'Warehouse / Store'),
    ], string='Property Type', default='commercial', required=True, tracking=True)
    state = fields.Selection([
        ('vacant', 'Vacant / Available'),
        ('occupied', 'Occupied / Rented'),
        ('maintenance', 'Under Maintenance'),
    ], string='Status', default='vacant', required=True, tracking=True)

    street = fields.Char(string='Street / Plot')
    neighborhood = fields.Char(string='Floor / Level', help='Floor of the commercial building, e.g. Basement, Ground Floor, 1st Floor')
    city = fields.Char(string='City', default='Kigali')
    bedrooms = fields.Integer(string='Bedrooms', default=3)
    bathrooms = fields.Integer(string='Bathrooms', default=2)
    area_sqm = fields.Float(string='Area (sq.m)')
    furnished = fields.Boolean(string='Furnished')

    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    currency_id = fields.Many2one(
        'res.currency',
        string='Rent Currency',
        default=lambda self: self.env.company.currency_id,
        required=True,
        help='Choose RWF or USD for this property',
    )
    monthly_rent = fields.Monetary(string='Monthly Rent', currency_field='currency_id', required=True, tracking=True)
    security_deposit = fields.Monetary(string='Security Deposit', currency_field='currency_id')

    current_tenant_id = fields.Many2one('res.partner', string='Current Tenant', tracking=True)
    lease_ids = fields.One2many('isange.lease', 'property_id', string='Tenant Leases')
    expense_ids = fields.One2many('isange.property.expense', 'property_id', string='Maintenance & Expenses')
    invoice_ids = fields.One2many('account.move', 'isange_property_id', string='Rent Invoices')

    lease_count = fields.Integer(compute='_compute_counts', string='Leases')
    expense_count = fields.Integer(compute='_compute_counts', string='Expenses')
    invoice_count = fields.Integer(compute='_compute_counts', string='Invoices')

    total_invoiced = fields.Monetary(compute='_compute_financials', string='Total Rent Invoiced', currency_field='currency_id')
    total_expenses = fields.Monetary(compute='_compute_financials', string='Total Maintenance & Expenses', currency_field='currency_id')
    net_profit = fields.Monetary(compute='_compute_financials', string='Net Profit', currency_field='currency_id')
    notes = fields.Text(string='House Notes & Meter Numbers')

    @api.depends('lease_ids', 'expense_ids', 'invoice_ids')
    def _compute_counts(self):
        for rec in self:
            rec.lease_count = len(rec.lease_ids)
            rec.expense_count = len(rec.expense_ids)
            rec.invoice_count = len(rec.invoice_ids.filtered(lambda m: m.move_type == 'out_invoice'))

    @api.depends('invoice_ids.state', 'invoice_ids.amount_total', 'expense_ids.state', 'expense_ids.amount')
    def _compute_financials(self):
        for rec in self:
            posted_invoices = rec.invoice_ids.filtered(
                lambda m: m.move_type == 'out_invoice' and m.state == 'posted'
            )
            approved_expenses = rec.expense_ids.filtered(lambda e: e.state == 'approved')
            rec.total_invoiced = sum(posted_invoices.mapped('amount_total'))
            rec.total_expenses = sum(approved_expenses.mapped('amount'))
            rec.net_profit = rec.total_invoiced - rec.total_expenses

    @api.model
    def get_dashboard_currency_info(self):
        """Used by the Command Center: conversion rates of active currencies into
        the company currency, so that RWF / USD amounts can be summed safely."""
        company = self.env.company
        company_currency = company.currency_id
        today = fields.Date.context_today(self)
        Currency = self.env['res.currency']
        active_currencies = Currency.search([('active', '=', True)]) | company_currency
        rates = {}
        for currency in active_currencies:
            rates[currency.id] = (
                1.0
                if currency.id == company_currency.id
                else Currency._get_conversion_rate(currency, company_currency, company, today)
            )
        return {
            'name': company_currency.name,
            'symbol': company_currency.symbol,
            'rates': rates,
        }

    @api.model
    def get_command_center_data(self, six_start_utc=False):
        """Single-trip bootstrap payload for the Command Center, Quick Sale,
        Stock Manager, and Vehicle Manager — collapses 17 browser HTTP RPCs
        into 1 server-side round-trip."""
        def _safe_search_read(model_name, domain, fields_list, order=None, limit=None):
            if model_name not in self.env:
                return []
            try:
                kwargs = {}
                if order:
                    kwargs['order'] = order
                if limit:
                    kwargs['limit'] = limit
                return self.env[model_name].search_read(domain, fields_list, **kwargs)
            except Exception:
                return []

        date_domain = [('date_order', '>=', six_start_utc)] if six_start_utc else []

        return {
            'currencyInfo': self.get_dashboard_currency_info(),
            'units': _safe_search_read(
                'isange.property',
                [('property_type', '!=', 'house')],
                ['name', 'code', 'neighborhood', 'state', 'monthly_rent', 'current_tenant_id',
                 'net_profit', 'area_sqm', 'property_type', 'currency_id'],
                order='code asc',
            ),
            'leases': _safe_search_read(
                'isange.lease',
                [('state', '=', 'active')],
                ['name', 'property_id', 'tenant_id', 'rent_amount', 'next_invoice_date',
                 'billing_cycle', 'currency_id'],
                order='next_invoice_date asc',
            ),
            'rentInvoices': _safe_search_read(
                'account.move',
                [('isange_property_id', '!=', False), ('move_type', '=', 'out_invoice'), ('state', '=', 'posted')],
                ['name', 'partner_id', 'amount_total_signed', 'amount_residual_signed',
                 'invoice_date', 'invoice_date_due', 'payment_state', 'isange_property_id'],
                order='invoice_date desc',
            ),
            'sales': _safe_search_read(
                'sale.order',
                [('state', 'in', ['sale', 'done'])] + date_domain,
                ['amount_total', 'date_order', 'currency_id'],
            ),
            'posOrders': _safe_search_read(
                'pos.order',
                [('state', 'in', ['paid', 'done', 'invoiced'])] + date_domain,
                ['amount_total', 'date_order', 'currency_id'],
            ),
            'purchases': _safe_search_read(
                'purchase.order',
                [('state', 'in', ['purchase', 'done'])] + date_domain,
                ['amount_total', 'date_order', 'currency_id'],
            ),
            'trips': _safe_search_read(
                'isange.vehicle.trip',
                [],
                ['name', 'date', 'date_arrival', 'vehicle_id', 'driver_id', 'partner_id',
                 'sale_order_id', 'origin_location', 'destination_location', 'cargo_description',
                 'trip_type', 'state', 'odometer_start', 'odometer_end', 'distance_km',
                 'fuel_liters', 'fuel_cost', 'other_cost', 'total_cost', 'delivery_fee',
                 'notes', 'currency_id'],
                order='date desc, id desc',
                limit=500,
            ),
            'vehicles': _safe_search_read(
                'fleet.vehicle',
                [],
                ['name', 'license_plate', 'driver_id', 'odometer', 'odometer_unit',
                 'model_id', 'state_id', 'fuel_type'],
                order='id asc',
                limit=50,
            ),
            'fleetServices': _safe_search_read(
                'fleet.vehicle.log.services',
                [],
                ['vehicle_id', 'service_type_id', 'date', 'amount', 'currency_id',
                 'odometer', 'vendor_id', 'purchaser_id', 'description', 'notes', 'state', 'inv_ref'],
                order='date desc, id desc',
                limit=500,
            ),
            'fleetServiceTypes': _safe_search_read(
                'fleet.service.type',
                [('category', '=', 'service')],
                ['id', 'name', 'category'],
                order='id asc',
                limit=30,
            ),
            'products': _safe_search_read(
                'product.product',
                [('sale_ok', '=', True), ('type', '!=', 'service')],
                ['id', 'name', 'default_code', 'list_price', 'standard_price',
                 'qty_available', 'free_qty', 'incoming_qty', 'outgoing_qty',
                 'is_storable', 'uom_id', 'construction_category', 'taxes_id', 'write_date'],
                order='name asc',
                limit=80,
            ),
            'partners': _safe_search_read(
                'res.partner',
                [('active', '=', True)],
                ['id', 'name', 'phone', 'city'],
                order='id desc',
                limit=30,
            ),
            'journals': _safe_search_read(
                'account.journal',
                [('type', 'in', ['cash', 'bank'])],
                ['id', 'name', 'type'],
            ),
            'taxes': _safe_search_read(
                'account.tax',
                [('type_tax_use', '=', 'sale'), ('active', '=', True), ('amount', '>', 0)],
                ['id', 'name', 'amount'],
                limit=5,
            ),
            'locations': _safe_search_read(
                'stock.location',
                [('usage', '=', 'internal')],
                ['id', 'name', 'complete_name'],
                order='id asc',
                limit=5,
            ),
        }

    @api.model
    def execute_fast_sale(self, payload):
        """Execute the entire Quick Sale checkout (Order + Stock Deduction +
        Posted Invoice + Single/Split Payment + Optional Delivery Trip) in ONE
        atomic server-side RPC instead of 15+ sequential client round-trips."""
        mode = payload.get('mode') or 'cash'
        partner_id = int(payload.get('partner_id') or 0)
        new_cust_name = (payload.get('new_customer_name') or '').strip()
        new_cust_phone = (payload.get('new_customer_phone') or '').strip()
        if new_cust_name:
            partner = self.env['res.partner'].create({
                'name': new_cust_name,
                'phone': new_cust_phone or False,
                'city': 'Kigali',
            })
            partner_id = partner.id

        if not partner_id:
            raise UserError(_('Please select a customer before completing the sale.'))

        cart = payload.get('cart') or []
        if not cart:
            raise UserError(_('Add at least one item to the cart first.'))

        # Remember prices typed for 0-priced items
        Product = self.env['product.product']
        for item in cart:
            if not item.get('original_price') and float(item.get('price') or 0) > 0:
                Product.browse(int(item['id'])).write({'list_price': float(item['price'])})

        tax_ids = [int(t) for t in (payload.get('tax_ids') or []) if t]
        order_lines = [
            (0, 0, {
                'product_id': int(item['id']),
                'name': item.get('name') or 'Item',
                'product_uom_qty': float(item.get('qty') or 1),
                'price_unit': float(item.get('price') or 0),
                'tax_ids': [(6, 0, tax_ids)],
            })
            for item in cart
        ]

        sale_order = self.env['sale.order'].create({
            'partner_id': partner_id,
            'order_line': order_lines,
        })

        partner_rec = self.env['res.partner'].browse(partner_id)
        customer_name = partner_rec.name or 'Customer'

        if mode == 'quotation':
            return {
                'so_id': sale_order.id,
                'so_name': sale_order.name,
                'inv_id': False,
                'inv_name': '',
                'partner_id': partner_id,
                'customer': customer_name,
                'badge': 'Quotation / Pro-Forma',
            }

        # Confirm SO and auto-validate outgoing pickings
        sale_order.action_confirm()
        for picking in sale_order.picking_ids:
            if picking.state in ('done', 'cancel'):
                continue
            try:
                for move in picking.move_ids:
                    move.write({'quantity': move.product_uom_qty, 'picked': True})
                picking.with_context(
                    skip_backorder=True,
                    picking_ids_not_to_backorder=picking.ids,
                ).button_validate()
            except Exception:
                pass

        # Create & post linked invoice
        inv_lines = [
            (0, 0, {
                'product_id': line.product_id.id,
                'name': line.name,
                'quantity': line.product_uom_qty,
                'price_unit': line.price_unit,
                'tax_ids': [(6, 0, line.tax_ids.ids)],
                'sale_line_ids': [(6, 0, [line.id])],
            })
            for line in sale_order.order_line
        ]
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': partner_id,
            'invoice_date': payload.get('invoice_date') or fields.Date.context_today(self),
            'invoice_origin': sale_order.name,
            'invoice_line_ids': inv_lines,
        })
        invoice.action_post()

        journals = payload.get('journals') or {}
        pay_ctx = {'active_model': 'account.move', 'active_ids': [invoice.id]}
        payment_badge = 'Credit (Unpaid Invoice)'

        if mode in ('cash', 'momo', 'bank'):
            journal_id = journals.get(mode) or journals.get('cash') or journals.get('momo') or journals.get('bank')
            if journal_id:
                wiz = self.env['account.payment.register'].with_context(**pay_ctx).create({
                    'journal_id': int(journal_id),
                })
                wiz.with_context(**pay_ctx).action_create_payments()
            payment_badge = (
                'Paid — Cash' if mode == 'cash'
                else 'Paid — MTN MoMo' if mode == 'momo'
                else 'Paid — Bank'
            )
        elif mode == 'split':
            split = payload.get('split') or {}
            legs = [
                ('cash', 'Cash', max(0.0, float(split.get('cash') or 0))),
                ('momo', 'MoMo', max(0.0, float(split.get('momo') or 0))),
                ('bank', 'Bank', max(0.0, float(split.get('bank') or 0))),
            ]
            legs = [leg for leg in legs if leg[2] > 0]
            paid_sum = 0.0
            parts = []
            for key, label, amt in legs:
                journal_id = journals.get(key) or journals.get('cash') or journals.get('momo') or journals.get('bank')
                if journal_id:
                    wiz = self.env['account.payment.register'].with_context(**pay_ctx).create({
                        'journal_id': int(journal_id),
                        'amount': amt,
                        'payment_difference_handling': 'open',
                    })
                    wiz.with_context(**pay_ctx).action_create_payments()
                    paid_sum += amt
                    parts.append(f"{label} {int(round(amt)):,}")
            if parts:
                total_amt = float(payload.get('total_amount') or invoice.amount_total or 0)
                rem = max(0.0, total_amt - paid_sum)
                joined = ' + '.join(parts)
                payment_badge = (
                    f"Partial — {joined} (Due {int(round(rem)):,})"
                    if rem > 0.5
                    else f"Split — {joined}"
                )

        # Optional 1-click delivery trip
        if payload.get('add_delivery') and payload.get('vehicle_id'):
            try:
                vehicle = self.env['fleet.vehicle'].browse(int(payload['vehicle_id']))
                driver_id = vehicle.driver_id.id or partner_id
                cargo = ', '.join(f"{item.get('qty')}x {item.get('name')}" for item in cart)
                self.env['isange.vehicle.trip'].create({
                    'vehicle_id': vehicle.id,
                    'driver_id': driver_id,
                    'trip_type': 'delivery',
                    'sale_order_id': sale_order.id,
                    'partner_id': partner_id,
                    'destination_location': (payload.get('destination') or '').strip() or f"{customer_name} Site, Kigali",
                    'cargo_description': cargo,
                    'delivery_fee': float(payload.get('delivery_fee') or 0),
                    'state': 'in_transit',
                })
            except Exception:
                pass

        return {
            'so_id': sale_order.id,
            'so_name': sale_order.name,
            'inv_id': invoice.id,
            'inv_name': invoice.name,
            'partner_id': partner_id,
            'customer': customer_name,
            'badge': payment_badge,
        }

    @api.model
    def execute_stock_adjustment(self, payload):
        """Execute a stock adjustment or supplier PO restock + receipt validation
        in ONE atomic server-side RPC."""
        product_id = int(payload['product_id'])
        loc_id = int(payload.get('location_id') or 5)
        target_qty = max(0.0, float(payload.get('target_qty') or 0))
        delta = float(payload.get('delta') or 0)
        label = (payload.get('label') or '').strip()
        use_po = bool(payload.get('use_po'))
        supplier_id = int(payload.get('supplier_id') or 0)

        product = self.env['product.product'].browse(product_id)
        if not product.is_storable:
            product.write({'is_storable': True})

        done_via_po = False
        if use_po and supplier_id and delta > 0:
            po = self.env['purchase.order'].create({
                'partner_id': supplier_id,
                'order_line': [(0, 0, {
                    'product_id': product_id,
                    'name': product.name,
                    'product_qty': delta,
                    'price_unit': float(payload.get('unit_cost') or product.standard_price or 0),
                })],
            })
            po.button_confirm()
            if po.picking_ids:
                for picking in po.picking_ids:
                    if picking.state in ('done', 'cancel'):
                        continue
                    for move in picking.move_ids:
                        move.write({'quantity': move.product_uom_qty, 'picked': True})
                    picking.with_context(
                        skip_backorder=True,
                        picking_ids_not_to_backorder=picking.ids,
                    ).button_validate()
                done_via_po = True

        if not done_via_po:
            Quant = self.env['stock.quant']
            quant = Quant.search([('product_id', '=', product_id), ('location_id', '=', loc_id)], limit=1)
            if quant:
                quant.write({'inventory_quantity': target_qty})
            else:
                quant = Quant.create({
                    'product_id': product_id,
                    'location_id': loc_id,
                    'inventory_quantity': target_qty,
                })
            ctx = {'inventory_name': label} if label else {}
            quant.with_context(**ctx).action_apply_inventory()

        return {'ok': True, 'product_id': product_id}

    def action_new_lease(self):
        """One-click button on a house to create a new Tenant Lease."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('New Tenant Lease'),
            'res_model': 'isange.lease',
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'default_property_id': self.id,
                'default_currency_id': self.currency_id.id,
                'default_rent_amount': self.monthly_rent,
                'default_deposit_amount': self.security_deposit,
            },
        }

    def action_new_expense(self):
        """One-click button on a house to log a repair or utility bill."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Log House Repair / Expense'),
            'res_model': 'isange.property.expense',
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'default_property_id': self.id,
                'default_currency_id': self.currency_id.id,
            },
        }

    def action_set_vacant(self):
        self.write({'state': 'vacant', 'current_tenant_id': False})

    def action_set_maintenance(self):
        self.write({'state': 'maintenance'})

    def action_view_leases(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Tenant Leases'),
            'res_model': 'isange.lease',
            'view_mode': 'list,form',
            'domain': [('property_id', '=', self.id)],
            'context': {
                'default_property_id': self.id,
                'default_currency_id': self.currency_id.id,
                'default_rent_amount': self.monthly_rent,
                'default_deposit_amount': self.security_deposit,
            },
        }

    def action_view_expenses(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Property Maintenance & Expenses'),
            'res_model': 'isange.property.expense',
            'view_mode': 'list,form',
            'domain': [('property_id', '=', self.id)],
            'context': {
                'default_property_id': self.id,
                'default_currency_id': self.currency_id.id,
            },
        }

    def action_view_invoices(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Rent Invoices'),
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [('isange_property_id', '=', self.id), ('move_type', '=', 'out_invoice')],
            'context': {'default_isange_property_id': self.id, 'default_move_type': 'out_invoice'},
        }


class IsangeLease(models.Model):
    _name = 'isange.lease'
    _description = 'Tenant Lease Contract'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_start desc, id desc'

    name = fields.Char(string='Lease Reference', required=True, copy=False, readonly=True, default=lambda self: _('New'))
    property_id = fields.Many2one('isange.property', string='Property / House', required=True, tracking=True)
    tenant_id = fields.Many2one('res.partner', string='Tenant Name', required=True, tracking=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        default=lambda self: self.env.company.currency_id,
        required=True,
    )

    date_start = fields.Date(string='Move-In / Start Date', default=fields.Date.context_today, required=True, tracking=True)
    date_end = fields.Date(string='Move-Out / End Date', tracking=True)
    next_invoice_date = fields.Date(string='Next Rent Due Date', default=fields.Date.context_today, tracking=True)

    billing_cycle = fields.Selection([
        ('monthly', 'Monthly'),
        ('quarterly', 'Quarterly (3 Months)'),
        ('yearly', 'Yearly (12 Months)'),
    ], string='Payment Frequency', default='monthly', required=True)

    rent_amount = fields.Monetary(string='Rent Amount', currency_field='currency_id', required=True, tracking=True)
    deposit_amount = fields.Monetary(string='Security Deposit', currency_field='currency_id')
    deposit_paid = fields.Boolean(string='Deposit Paid?', tracking=True)

    state = fields.Selection([
        ('draft', '1. Draft'),
        ('active', '2. Active / Rented'),
        ('expired', 'Expired'),
        ('terminated', 'Ended'),
    ], string='Status', default='draft', required=True, tracking=True)

    invoice_ids = fields.One2many('account.move', 'isange_lease_id', string='Rent Invoices')
    invoice_count = fields.Integer(compute='_compute_invoice_count', string='Invoices')
    terms = fields.Text(string='Lease Notes & Special Agreements')

    @api.depends('invoice_ids')
    def _compute_invoice_count(self):
        for rec in self:
            rec.invoice_count = len(rec.invoice_ids)

    @api.onchange('property_id')
    def _onchange_property_id(self):
        if self.property_id:
            self.currency_id = self.property_id.currency_id
            self.rent_amount = self.property_id.monthly_rent
            self.deposit_amount = self.property_id.security_deposit

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('isange.lease') or _('New')
        return super().create(vals_list)

    def action_activate(self):
        for rec in self:
            rec.state = 'active'
            rec.property_id.write({
                'state': 'occupied',
                'current_tenant_id': rec.tenant_id.id,
            })

    def action_terminate(self):
        for rec in self:
            rec.state = 'terminated'
            if rec.property_id.current_tenant_id == rec.tenant_id:
                rec.property_id.write({
                    'state': 'vacant',
                    'current_tenant_id': False,
                })

    def action_create_invoice(self):
        self.ensure_one()
        if self.rent_amount <= 0:
            raise UserError(_('Please enter a positive Rent Amount before creating an invoice.'))

        period_date = self.next_invoice_date or fields.Date.context_today(self)
        cycle_label = dict(self._fields['billing_cycle'].selection).get(self.billing_cycle, 'Monthly')
        description = _('Rent for %s (%s - Due %s)') % (
            self.property_id.name,
            cycle_label,
            period_date,
        )

        invoice_vals = {
            'move_type': 'out_invoice',
            'partner_id': self.tenant_id.id,
            'currency_id': self.currency_id.id,
            'invoice_date': fields.Date.context_today(self),
            'isange_lease_id': self.id,
            'isange_property_id': self.property_id.id,
            'invoice_origin': self.name,
            'invoice_line_ids': [(0, 0, {
                'name': description,
                'quantity': 1.0,
                'price_unit': self.rent_amount,
            })],
        }
        invoice = self.env['account.move'].create(invoice_vals)

        if self.billing_cycle == 'monthly':
            self.next_invoice_date = period_date + relativedelta(months=1)
        elif self.billing_cycle == 'quarterly':
            self.next_invoice_date = period_date + relativedelta(months=3)
        elif self.billing_cycle == 'yearly':
            self.next_invoice_date = period_date + relativedelta(years=1)

        return {
            'type': 'ir.actions.act_window',
            'name': _('Rent Invoice'),
            'res_model': 'account.move',
            'res_id': invoice.id,
            'view_mode': 'form',
        }

    def action_view_invoices(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Lease Invoices'),
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [('isange_lease_id', '=', self.id)],
            'context': {
                'default_isange_lease_id': self.id,
                'default_isange_property_id': self.property_id.id,
                'default_partner_id': self.tenant_id.id,
                'default_currency_id': self.currency_id.id,
                'default_move_type': 'out_invoice',
            },
        }


class IsangePropertyExpense(models.Model):
    _name = 'isange.property.expense'
    _description = 'Property Maintenance & Expense'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'

    name = fields.Char(string='What was repaired or paid?', required=True, tracking=True)
    property_id = fields.Many2one('isange.property', string='Property / House', required=True, tracking=True)
    expense_type = fields.Selection([
        ('repair', 'Maintenance & Repair'),
        ('utility', 'Water / Electricity Bill'),
        ('tax', 'Property Tax / Insurance'),
        ('cleaning', 'Cleaning & Security'),
        ('other', 'Other Expense'),
    ], string='Category', default='repair', required=True, tracking=True)

    vendor_id = fields.Many2one('res.partner', string='Technician / Vendor')
    date = fields.Date(string='Date', default=fields.Date.context_today, required=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        default=lambda self: self.env.company.currency_id,
        required=True,
    )
    amount = fields.Monetary(string='Cost Amount', currency_field='currency_id', required=True, tracking=True)
    state = fields.Selection([
        ('draft', 'Pending'),
        ('approved', 'Paid / Approved'),
    ], string='Status', default='approved', required=True, tracking=True)
    notes = fields.Text(string='Additional Notes')

    def action_approve(self):
        self.write({'state': 'approved'})


class AccountMovePropertyExtend(models.Model):
    _inherit = 'account.move'

    isange_property_id = fields.Many2one('isange.property', string='Rental Property', index=True)
    isange_lease_id = fields.Many2one('isange.lease', string='Tenant Lease', index=True)
