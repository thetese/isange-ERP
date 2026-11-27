# -*- coding: utf-8 -*-
{
    'name': 'Isange Property Rentals & Fleet Operations',
    'version': '19.0.2.2.0',
    'category': 'Operations',
    'sequence': 1,
    'summary': 'Visual Command Center, Quick Sale Terminal, Stock Manager, Vehicle & Fleet Control, Commercial Rentals & Pro-Forma PDFs',
    'description': """
Isange Hardware Ltd — Command Center, Commercial Building Rentals & Fleet Operations
====================================================================================
Unified Odoo 19 module built for Isange Hardware Ltd (Gisozi, Kigali, Rwanda) covering:
1. Interactive Visual Command Center (KPI Strip, Quick 20s Sale Terminal with Cash/MoMo/Bank Split Settlement, 2-Pane Stock & Inventory Control, 2-Pane Vehicle & Fleet P&L Manager, Commercial Building Stacking Plan, Occupancy & Revenue Attribution Gauges)
2. Commercial Building Rentals (Units, Shops, Offices, Warehouses, Tenant Leases, One-Click Rent Invoicing, Building Repairs & Net Operating Income)
3. Vehicle Operations & Delivery Tracking (Company Delivery Trips, 1-Click Sales Order Dispatch, Odometer & Fuel Cost Tracking, Fleet Maintenance Service Logs)
4. Professional Rwanda TIN & Multi-Bank Pro-Forma / Invoice Document Footer (MTN MoMo, BK, Equity USD/RWF, BPR, NCBA) & All-in-One Executive Reports Hub
    """,
    'author': 'Isange Hardware Ltd',
    'license': 'LGPL-3',
    'icon': '/isange_property_fleet/static/description/icon.svg',
    'depends': [
        'base',
        'web',
        'mail',
        'contacts',
        'sale_management',
        'purchase',
        'stock',
        'account',
        'point_of_sale',
        'fleet',
        'maintenance',
        'hr_expense',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'data/product_photos_data.xml',
        'views/property_rental_views.xml',
        'views/vehicle_trip_views.xml',
        'views/product_construction_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'isange_property_fleet/static/src/scss/isange_dashboard.scss',
            'isange_property_fleet/static/src/js/isange_dashboard.js',
            'isange_property_fleet/static/src/xml/isange_dashboard.xml',
        ],
        'web.assets_web_dark': [
            'isange_property_fleet/static/src/scss/isange_dashboard.dark.scss',
        ],
    },
    'installable': True,
    'application': True,
    'auto_install': False,
}
