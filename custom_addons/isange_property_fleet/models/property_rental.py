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
        """Used by the Command Center: conversion rates of every currency into
        the company currency, so that RWF / USD amounts can be summed safely."""
        company = self.env.company
        company_currency = company.currency_id
        today = fields.Date.context_today(self)
        Currency = self.env['res.currency']
        rates = {}
        for currency in Currency.with_context(active_test=False).search([]):
            rates[currency.id] = Currency._get_conversion_rate(currency, company_currency, company, today)
        return {
            'name': company_currency.name,
            'symbol': company_currency.symbol,
            'rates': rates,
        }

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
