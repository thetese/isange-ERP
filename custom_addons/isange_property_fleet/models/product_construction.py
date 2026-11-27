# -*- coding: utf-8 -*-
from odoo import fields, models


class ProductTemplateConstruction(models.Model):
    _inherit = 'product.template'

    is_construction_item = fields.Boolean(string='Construction / Hardware Item', default=True)
    construction_category = fields.Selection([
        ('cement_aggregates', 'Cement, Sand & Aggregates'),
        ('steel_iron', 'Steel Bars, Iron Sheets & Nails'),
        ('timber_roofing', 'Timber, Wood & Roofing'),
        ('plumbing', 'Plumbing, Pipes & Tanks'),
        ('electrical', 'Electrical & Lighting'),
        ('paint_finishing', 'Paint, Tiles & Finishing'),
        ('tools_hardware', 'Tools, Locks & General Hardware'),
    ], string='Construction Category', default='tools_hardware')
    unit_weight_kg = fields.Float(string='Unit Weight (kg)', help='Weight per unit for delivery vehicle load calculation')
    bulk_pack_qty = fields.Float(string='Wholesale / Bulk Pack Qty', help='Number of units in a bundle, box, or pallet')
