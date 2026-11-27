# -*- coding: utf-8 -*-
from odoo import api, fields, models, _


class IsangeVehicleTrip(models.Model):
    _name = 'isange.vehicle.trip'
    _description = 'Company Delivery & Vehicle Operation Log'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'

    name = fields.Char(string='Trip Reference', required=True, copy=False, readonly=True, default=lambda self: _('New'))
    vehicle_id = fields.Many2one('fleet.vehicle', string='Vehicle', required=True, tracking=True)
    driver_id = fields.Many2one('res.partner', string='Driver', required=True, tracking=True)
    date = fields.Datetime(string='Departure Time', default=fields.Datetime.now, required=True, tracking=True)
    date_arrival = fields.Datetime(string='Return Time', tracking=True)

    trip_type = fields.Selection([
        ('delivery', 'Customer Construction Delivery'),
        ('pickup', 'Supplier Stock Pickup'),
        ('property', 'House / Property Maintenance Visit'),
        ('internal', 'General Company Operation'),
    ], string='Operation Type', default='delivery', required=True, tracking=True)

    partner_id = fields.Many2one('res.partner', string='Customer / Supplier')
    sale_order_id = fields.Many2one('sale.order', string='Linked Sales Order')
    origin_location = fields.Char(string='From (Origin)', default='Isange Hardware Store')
    destination_location = fields.Char(string='To (Destination)', required=True)
    cargo_description = fields.Char(string='Items / Cargo Carried')

    odometer_start = fields.Float(string='Start Odometer (km)')
    odometer_end = fields.Float(string='End Odometer (km)')
    distance_km = fields.Float(compute='_compute_distance', string='Distance (km)', store=True)

    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        default=lambda self: self.env.company.currency_id,
        required=True,
    )
    fuel_liters = fields.Float(string='Fuel Added (Liters)')
    fuel_cost = fields.Monetary(string='Fuel Cost', currency_field='currency_id', tracking=True)
    other_cost = fields.Monetary(string='Road / Loading / Repair Cost', currency_field='currency_id')
    total_cost = fields.Monetary(compute='_compute_total_cost', string='Total Trip Cost', currency_field='currency_id', store=True)
    delivery_fee = fields.Monetary(string='Delivery Fee Charged', currency_field='currency_id')

    state = fields.Selection([
        ('planned', '1. Planned'),
        ('in_transit', '2. On the Road'),
        ('completed', '3. Completed'),
        ('cancelled', 'Cancelled'),
    ], string='Status', default='planned', required=True, tracking=True)
    notes = fields.Text(string='Notes')

    @api.depends('odometer_start', 'odometer_end')
    def _compute_distance(self):
        for rec in self:
            if rec.odometer_end and rec.odometer_start and rec.odometer_end > rec.odometer_start:
                rec.distance_km = rec.odometer_end - rec.odometer_start
            else:
                rec.distance_km = 0.0

    @api.depends('fuel_cost', 'other_cost')
    def _compute_total_cost(self):
        for rec in self:
            rec.total_cost = (rec.fuel_cost or 0.0) + (rec.other_cost or 0.0)

    @api.onchange('vehicle_id')
    def _onchange_vehicle_id(self):
        if self.vehicle_id:
            if self.vehicle_id.driver_id:
                self.driver_id = self.vehicle_id.driver_id
            if self.vehicle_id.odometer:
                self.odometer_start = self.vehicle_id.odometer

    @api.onchange('sale_order_id')
    def _onchange_sale_order_id(self):
        if self.sale_order_id:
            self.partner_id = self.sale_order_id.partner_id
            if not self.destination_location and self.sale_order_id.partner_id:
                addr_parts = [
                    p for p in [self.sale_order_id.partner_id.street, self.sale_order_id.partner_id.city] if p
                ]
                self.destination_location = ', '.join(addr_parts) or self.sale_order_id.partner_id.name
            if not self.cargo_description and self.sale_order_id.order_line:
                items = [
                    f"{int(line.product_uom_qty)}x {line.product_id.name}"
                    for line in self.sale_order_id.order_line
                    if line.product_id
                ]
                if items:
                    self.cargo_description = ', '.join(items[:8])

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('isange.vehicle.trip') or _('New')
        return super().create(vals_list)

    def action_start_trip(self):
        self.write({'state': 'in_transit'})

    def action_complete_trip(self):
        for rec in self:
            vals = {'state': 'completed'}
            if not rec.date_arrival:
                vals['date_arrival'] = fields.Datetime.now()
            rec.write(vals)
            if rec.odometer_end and rec.vehicle_id and rec.odometer_end > (rec.vehicle_id.odometer or 0.0):
                rec.vehicle_id.odometer = rec.odometer_end

    def action_cancel_trip(self):
        self.write({'state': 'cancelled'})


class FleetVehicleExtend(models.Model):
    _inherit = 'fleet.vehicle'

    isange_trip_ids = fields.One2many('isange.vehicle.trip', 'vehicle_id', string='Delivery & Operation Trips')
    isange_trip_count = fields.Integer(compute='_compute_isange_trip_count', string='Trips')

    @api.depends('isange_trip_ids')
    def _compute_isange_trip_count(self):
        for rec in self:
            rec.isange_trip_count = len(rec.isange_trip_ids)

    def action_view_isange_trips(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Vehicle Delivery & Operation Trips'),
            'res_model': 'isange.vehicle.trip',
            'view_mode': 'list,form',
            'domain': [('vehicle_id', '=', self.id)],
            'context': {
                'default_vehicle_id': self.id,
                'default_driver_id': self.driver_id.id if self.driver_id else False,
                'default_odometer_start': self.odometer,
            },
        }


class SaleOrderVehicleExtend(models.Model):
    _inherit = 'sale.order'

    isange_trip_ids = fields.One2many('isange.vehicle.trip', 'sale_order_id', string='Vehicle Deliveries')
    isange_trip_count = fields.Integer(compute='_compute_isange_trip_count', string='Vehicle Trips')

    @api.depends('isange_trip_ids')
    def _compute_isange_trip_count(self):
        for rec in self:
            rec.isange_trip_count = len(rec.isange_trip_ids)

    def action_create_vehicle_trip(self):
        """One-click button on a Sales Order to dispatch a delivery vehicle."""
        self.ensure_one()
        addr_parts = [p for p in [self.partner_id.street, self.partner_id.city] if p]
        destination = ', '.join(addr_parts) or self.partner_id.name or ''
        items = [
            f"{int(line.product_uom_qty)}x {line.product_id.name}"
            for line in self.order_line
            if line.product_id
        ]
        cargo = ', '.join(items[:8])
        return {
            'type': 'ir.actions.act_window',
            'name': _('Dispatch Delivery Vehicle'),
            'res_model': 'isange.vehicle.trip',
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'default_trip_type': 'delivery',
                'default_sale_order_id': self.id,
                'default_partner_id': self.partner_id.id,
                'default_destination_location': destination,
                'default_cargo_description': cargo,
            },
        }

    def action_view_vehicle_trips(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Vehicle Deliveries'),
            'res_model': 'isange.vehicle.trip',
            'view_mode': 'list,form',
            'domain': [('sale_order_id', '=', self.id)],
            'context': {
                'default_sale_order_id': self.id,
                'default_partner_id': self.partner_id.id,
            },
        }
