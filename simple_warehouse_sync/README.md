# SyncVision

**Advanced Warehouse Synchronization Module for Odoo 19**

[![Odoo Version](https://img.shields.io/badge/Odoo-19.0-purple.svg)](https://www.odoo.com)
[![License](https://img.shields.io/badge/License-OPL--1-blue.svg)](https://www.odoo.com/documentation/19.0/legal/licenses.html)
[![Author](https://img.shields.io/badge/Author-Abolfazl%20Rezaei-green.svg)](https://github.com/abolfazlrezaei)

---

## Overview

**SyncVision** is a powerful Odoo module that seamlessly synchronizes external warehouse data with your Odoo inventory system. It provides real-time stock updates, comprehensive logging, and intelligent product management—all from an intuitive dashboard.

---

## Key Features

### Real-Time Warehouse Integration
- Seamlessly synchronize external warehouse data with Odoo inventory
- Automatic stock quantity updates based on configurable mapping rules
- Support for Bearer token authentication with external APIs

### Comprehensive Dashboard
- **Control Panel** with real-time statistics and sync status
- Visual indicators for sync health, API latency, and next scheduled run
- One-click access to all logs, reports, and product details

### Smart Product Management
- **Automatic Publishing/Unpublishing** based on SKU and barcode availability
- **Priority Brand Tracking** for important products
- **External ID Mapping** for seamless integration with third-party systems
- Brand synchronization from external warehouse data

### Advanced Logging & Monitoring
- Complete sync history with detailed logs
- **Low Stock Alerts** — Products below configurable threshold
- **High Stock Tracking** — Products at or above threshold
- **Missing Products** — API products not found in Odoo
- **Unpublished Products** — Items missing SKU or barcode

### Flexible Configuration
- Configurable **Stock Mapping Rules** (min/max/qty)
- Adjustable **Low Stock Threshold**
- **SKU Prefix Filtering** to ignore specific missing products
- **Priority Brands** configuration for important product tracking

### Automated Scheduling
- Configurable cron job intervals (minutes, hours, days, weeks, months)
- Enable/disable auto-sync from the dashboard
- Manual sync trigger with one-click operation

### Email Reporting
- Automated HTML email reports after each sync
- Detailed statistics with visual charts
- Customizable recipient list
- Professional dark-themed email template

---

## Installation

### Prerequisites
- Odoo 19.0
- Python 3.10+
- `requests` library (included in Odoo dependencies)

### Steps

1. **Download** the module and place it in your Odoo addons directory:
   ```bash
   cp -r simple_warehouse_sync /path/to/odoo/custom-addons/
   ```

2. **Update the addons path** in your Odoo configuration file:
   ```ini
   addons_path = /path/to/odoo/addons,/path/to/odoo/custom-addons
   ```

3. **Restart Odoo** and update the apps list:
   ```bash
   ./odoo-bin -u all -d your_database
   ```

4. **Install the module** from Apps menu:
   - Go to **Apps** → Search for "SyncVision"
   - Click **Install**

---

## Configuration

### API Settings

Navigate to **SyncVision** → **Control Panel** to configure:

| Setting | Description |
|---------|-------------|
| **Sync API URL** | External warehouse API endpoint |
| **API Token** | Bearer token for authentication |
| **API Timeout** | Request timeout in seconds (default: 60) |

### Stock Mapping Rules

Define how API quantities map to Odoo stock levels:

```
min,max,qty
0,0,0
0,30,0
30,50,2
50,200,5
200,,10
```

- Leave `max` empty for "no upper bound"
- Rules are evaluated in order; first match wins

### API Field Mapping

Configure which fields from your API response contain the SKU and stock quantity:

| Setting | Default | Description |
|---------|---------|-------------|
| **SKU Field Name** | `sku` | Field name in API response for product SKU |
| **Stock Quantity Field Name** | `southbayStock` | Field name in API response for stock quantity |

**Example:** If your API returns `{"product_code": "ABC", "qty": 50}`, set:
- SKU Field to `product_code`
- Stock Field to `qty`

### Scheduling

| Setting | Description |
|---------|-------------|
| **Cron Interval Number** | Frequency value (e.g., 1, 6, 24) |
| **Cron Interval Type** | Unit: minutes, hours, days, weeks, months |
| **Enable Auto-sync** | Toggle automatic synchronization |

### Notifications

| Setting | Description |
|---------|-------------|
| **Notification Recipients** | Comma-separated email addresses |
| **Priority Brands** | Comma-separated brand names for priority tracking |
| **Ignore Missing SKU Prefixes** | SKU prefixes to exclude from missing reports |
| **Low Stock Threshold** | Quantity below which products are flagged (default: 5) |

---

## Usage

### Saving Settings

1. Navigate to **SyncVision** → **Control Panel**
2. Modify any settings (API URL, Stock Mapping, API Field Mapping, etc.)
3. Click the **💾 Save Settings** button (orange)
4. Click the **🔄 Refresh Panel** button to see the updated values

> **Important:** After saving settings, you must click **Refresh Panel** to reload the dashboard and see your new configuration values.

### Manual Sync

1. Navigate to **SyncVision** → **Control Panel**
2. Click the **Start Manual Sync** button
3. View results in the dashboard statistics

### Viewing Logs

- **Sync Logs**: Complete history of all sync operations
- **Sync Lines**: Per-product details from the latest sync
- **Missing Products**: Products in API but not in Odoo
- **Low Stock**: Products below threshold
- **Unpublished**: Products missing SKU or barcode

### Dashboard Statistics

| Metric | Description |
|--------|-------------|
| **Total Synced** | Products successfully matched and updated |
| **Published** | Products with valid SKU and barcode |
| **Unpublished** | Products missing identifiers |
| **Low Stock** | Products below threshold |
| **High Stock** | Products at or above threshold |
| **Missing** | API products not found in Odoo |
| **Priority Missing** | Missing products from priority brands |

---

## API Requirements

The external API must return JSON in this format:

```json
{
  "success": true,
  "data": [
    {
      "sku": "PRODUCT-SKU-001",
      "southbayStock": 150,
      "brand": "BrandName",
      "itemNumber": "EXT-12345",
      "barcode": "1234567890123"
    }
  ]
}
```

### Required Fields (Configurable)

| Field | Type | Description |
|-------|------|-------------|
| `sku` | string | Product SKU (matched against Odoo `default_code`) — **field name is configurable** |
| `southbayStock` | number | Available quantity in external warehouse — **field name is configurable** |

> **Note:** The field names `sku` and `southbayStock` are defaults. You can configure different field names in the Control Panel under "API Field Mapping" to match your API's response structure.

### Optional Fields

| Field | Type | Description |
|-------|------|-------------|
| `brand` | string | Product brand name |
| `itemNumber` | string | External system ID |
| `barcode` | string | Product barcode |

---

## Technical Details

### Models

| Model | Description |
|-------|-------------|
| `simple.warehouse.sync` | Main sync controller with dashboard |
| `simple.warehouse.sync.log` | Sync operation logs |
| `simple.warehouse.sync.line` | Per-product sync details |

### Dependencies

- `web` — Odoo web framework
- `stock` — Inventory management

### Security

Access control is defined in `security/ir.model.access.csv` with appropriate permissions for inventory users and managers.

---

## Troubleshooting

### Common Issues

**Sync fails with "API request failed"**
- Verify the API URL is correct and accessible
- Check the API token is valid
- Increase the timeout if the API is slow

**Products not updating**
- Ensure SKUs match between API and Odoo (normalized comparison)
- Check stock mapping rules are configured correctly

**Emails not sending**
- Verify email recipients are configured
- Check Odoo outgoing mail server settings

### Logs

Check Odoo logs for detailed sync information:
```bash
grep "SimpleWarehouseSync" /var/log/odoo/odoo.log
```

---

## License

This module is licensed under the **Odoo Proprietary License v1.0 (OPL-1)**.

See [Odoo Licensing](https://www.odoo.com/documentation/19.0/legal/licenses.html) for more details.
