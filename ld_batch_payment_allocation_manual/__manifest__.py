# -*- coding: utf-8 -*-
{
    "name": "Ld Batch Payment Allocation Manual",
    "summary": "Create one payment and allocate to multiple invoices (QuickBooks-style)",
    "version": "19.0.1.1.0",
    "author": "FenixCR Solutions",
    "website": "https://www.fenixcrsolutions.com",
    "license": "LGPL-3",
    "category": "Accounting/Accounting",
    "depends": ["account"],
    "data": [
        "security/ir.model.access.csv",
        "views/batch_payment_wizard_views.xml",
        "views/menu_views.xml"
    ],
    "installable": True,
    "application": False
}
