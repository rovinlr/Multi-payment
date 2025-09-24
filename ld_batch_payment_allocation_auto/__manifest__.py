{
    'name': 'Ld Batch Payment Allocation Auto',
    'version': '1.0',
    'category': 'Accounting',
    'summary': 'Batch Payment Allocation (Auto vs Manual variant)',
    'author': 'FenixCR Solutions',
    'website': 'https://www.fenixcrsolutions.com',
    'depends': ['account'],
    'data': [
        'security/ir.model.access.csv',
        'views/batch_payment_wizard_views.xml',
        'views/menu_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
