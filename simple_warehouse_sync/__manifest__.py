{
    "name": "SyncVision",
    "version": "18.0.15.0",
    "description": """
        SyncVision - Advanced Warehouse Synchronization Module

        🔄 **Real-time Warehouse Integration**
        Seamlessly synchronize your external warehouse data with Odoo inventory in real-time. 
        Keep your stock levels accurate and up-to-date automatically.

        📊 **Comprehensive Logging & Monitoring**
        - Complete sync history with detailed logs
        - Low stock alerts (products < 5 units)
        - Missing products tracking (API products not in Odoo)
        - Unpublished products monitoring (missing SKU/barcode)
        - High stock products overview (≥ 5 units)

        🎯 **Smart Product Management**
        - Automatic stock quantity updates based on warehouse rules
        - Product publishing/unpublishing based on SKU and barcode availability
        - Brand synchronization from external warehouse system
        - External ID mapping for seamless integration

        ⚙️ **Flexible Configuration**
        - Manual sync trigger with one-click operation
        - Automated scheduled synchronization via cron jobs
        - Configurable missing products filtering by SKU prefixes
        - Email notification system for sync reports

        📧 **Email Reporting**
        - Automated email reports after each sync
        - Detailed statistics and product summaries
        - Customizable recipient settings
        - HTML formatted reports with complete sync data

        🔍 **Advanced Search & Filtering**
        - Search products by SKU, brand, barcode, or external ID
        - Filter by stock levels, sync dates, and product status
        - Group by various criteria for better organization
        - Real-time data refresh and panel updates

        💡 **Key Features**
        - Control Panel dashboard for quick overview
        - One-click access to all logs and reports
        - Product-specific actions (open product forms)
        - Comprehensive error handling and logging
        - Multi-brand support with brand-based filtering

        Perfect for businesses that need reliable warehouse-to-Odoo synchronization with 
        complete visibility and control over their inventory management process.
    """,
    "author": "Abolfazl Rezaei",
    "category": "Inventory",
    "license": "OPL-1",
    "price": 69.0,
    "currency": "EUR",
    "depends": ["web", "stock"],
    "data": [
        "security/ir.model.access.csv",
        "views/simple_sync_views.xml",
        "views/simple_sync_line_views.xml",
        "data/cron.xml",
    ],
    'images': [
        "static/description/main_1_screenshot.png",
        "static/description/main_1-2_screenshot.png",
    ],

    "assets": {
        "web.assets_backend": [
            "simple_warehouse_sync/static/src/js/syncvision_circle.js",
        ],
    },
    "installable": True,
    "application": True,
    'web_icon': 'simple_warehouse_sync/static/description/icon.png',
}
