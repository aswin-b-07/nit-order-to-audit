# Order-to-Audit System for a Small Food Manufacturer

> Order-to-Audit System for a Small Food Manufacturer (NIT Trichy CDI Winter Internship 2026), individual submission by Suriya.

---

## 1. Project Overview

This application is a production-grade, audit-ready Enterprise Resource Planning (ERP) and order-to-cash workflow system tailored for a small-scale food processing factory (manufacturing traditional packaged snacks such as Murukku). The system bridges raw material procurement, Bill of Materials (BOM) recipe management, batch production tracking with automated variance flagging, sales order workflows, GST-compliant tax invoicing, dispatch-driven inventory movements, payment tracking, and audit logging.

---

## 2. Setup and Run Guide

### Requirements File Location
The project dependencies are located in `backend/requirements.txt`. For reference, the dependency specification is:

```text
asgiref==3.13.0
charset-normalizer==3.5.3
Django==6.1.3
djangorestframework==3.18.3
pillow==13.0.0
reportlab==5.1.0
sqlparse==0.6.1
tzdata==2026.6
```

### Step-by-Step Setup

#### Windows (PowerShell)
```powershell
# 1. Create and activate a Python virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# 2. Install required packages
pip install -r backend\requirements.txt

# 3. Enter backend directory and apply migrations
cd backend
python manage.py migrate

# 4. Seed benchmark baseline data and system users
python manage.py seed_benchmark
python manage.py seed_users

# 5. (Optional) Run the automated benchmark simulation script
python manage.py run_benchmark

# 6. Start the development server
python manage.py runserver
```

#### Linux / macOS (Bash)
```bash
# 1. Create and activate a Python virtual environment
python3 -m venv venv
source venv/bin/activate

# 2. Install required packages
pip install -r backend/requirements.txt

# 3. Enter backend directory and apply migrations
cd backend
python manage.py migrate

# 4. Seed benchmark baseline data and system users
python manage.py seed_benchmark
python manage.py seed_users

# 5. (Optional) Run the automated benchmark simulation script
python manage.py run_benchmark

# 6. Start the development server
python manage.py runserver
```

### Single Run Commands

**Windows PowerShell (Single Command):**
```powershell
python -m venv venv; .\venv\Scripts\Activate.ps1; pip install -r backend\requirements.txt; cd backend; python manage.py migrate; python manage.py seed_benchmark; python manage.py seed_users; python manage.py runserver
```

**Linux / macOS (Single Command):**
```bash
python3 -m venv venv && source venv/bin/activate && pip install -r backend/requirements.txt && cd backend && python manage.py migrate && python manage.py seed_benchmark && python manage.py seed_users && python manage.py runserver
```

---

## 3. Test Logins and Role Permissions

All seeded test accounts share the password: **`Test@1234`**

| Username | Role | Permitted Actions & Responsibilities |
|---|---|---|
| `salesman_a` | **Salesman** | Place new customer sales orders (`/orders/new/`), view own submitted orders (`/orders/`), view customer directory, view product price list, view own sales summary (`/my-sales/`). Cannot approve orders, dispatch goods, manage invoices, record production, or view reports. |
| `salesman_b` | **Salesman** | Same capabilities as `salesman_a`. Enforces strict row-level isolation: cannot see orders submitted by `salesman_a`. |
| `admin1` | **Admin** | Order-to-cash administration: view all orders across all sales reps (`/orders/`), edit lines/prices on submitted orders, approve orders (with automated stock verification), reject orders with reason, generate GST tax invoices (`/invoices/`), record stock dispatches, log customer payments, cancel invoices via credit note, download invoice PDFs, view customer directory and pricing. **Does not have access to accounting reports or production screens.** |
| `production1` | **Production** | Record incoming raw material & packaging purchases (`/purchase/new/`), execute batch production runs against BOM (`/production/new/`), record overhead expenses (labour, power) on runs (`/production/<id>/`), inspect real-time inventory balances, analyze unit costing and margins. Cannot place orders, approve sales, or create invoices. |
| `accountant1` | **Accountant** | Financial and compliance reporting: access reporting suite (`/reports/`), download all 10 CSV reports, view all orders, view inventory balances and ledger, view batch costing, download invoice PDFs. **Strictly read-only:** cannot create or modify orders, invoices, dispatches, purchases, or runs. |
| `owner1` | **Owner** | Full executive audit visibility: view reports suite (`/reports/`), view all orders, inspect stock balances, review production runs and batch costing, download invoice PDFs. Carries `view_dashboard` permission in code (though an executive dashboard page is not yet implemented in `urls.py`). |

---

## 4. Technology Stack and Rationale

The solution is implemented using **Django 6 + SQLite** with server-rendered mobile-responsive HTML templates and **ReportLab** for GST-compliant PDF invoice generation. This stack was selected because a unified Django application guarantees strict ACID database transactions, relational integrity, and zero-setup deployment on SQLite without introducing the latency or maintenance overhead of external database servers or microservices. Server-rendered responsive templates eliminate frontend compilation build steps while providing a reliable interface for field sales representatives on mobile devices and operators on the factory floor.

---

## 5. Feature Matrix Against PDF Tiers

| Feature Tier | Feature Name | Status | Real Implementation Details |
|---|---|---|---|
| **Must have** | Product & BOM Management | **Done** | Defined in `BOM` and `BOMLine` models with expected yields and recipe component quantities. |
| **Must have** | Raw Material & Packaging Inward Purchase | **Done** | Implemented via `Purchase` model, `record_purchase` service, and web UI at `/purchase/new/`. Updates inventory via `StockLedger`. |
| **Must have** | Batch Production Tracking & Variance | **Done** | Implemented in `ProductionRun` and `ProductionRunLine`. Computes yield and line variance in basis points with 3% tolerance flagging. |
| **Must have** | Sales Order Workflow (Submit, Approve, Reject) | **Done** | Full state machine (`SUBMITTED` -> `APPROVED` / `REJECTED`). Stock availability re-checked before approval. |
| **Must have** | GST-Compliant Tax Invoicing | **Done** | Auto-determines intra-state (CGST+SGST) vs inter-state (IGST). Sequences numbers by Indian Financial Year (`INV/2026-27/0001`). |
| **Must have** | Dispatch & Stock Deductions | **Done** | Stock deducted strictly upon dispatch via append-only `StockLedger` using weighted-average issue value. |
| **Must have** | Customer Payments & Outstanding Tracking | **Done** | Records payment mode, date, and reference. Computes invoice-level and customer-level outstanding balances in integer paise. |
| **Must have** | Append-Only Audit Trail | **Done** | `StockLedger` and `AuditLog` enforce immutability at the model layer (`save()` and `delete()` raise `RuntimeError`). |
| **Must have** | PDF Invoice Generation | **Done** | Built with ReportLab (`build_invoice_pdf`), downloadable at `/invoices/<id>/pdf/`. |
| **Should have** | Production Costing & Overhead Allocation | **Done** | Direct labour and power expenses logged on runs; computes total cost, unit cost per good packet, and gross margin percentage. |
| **Should have** | Credit Note & Invoice Cancellation | **Done** | `CreditNote` cancels issued invoice, reverses stock dispatch via `SALE_RETURN` ledger rows; blocked if payments exist. |
| **Should have** | Financial & Inventory Reporting Suite | **Done** | 10 distinct reports with date range filtering and CSV export at `/reports/`. |
| **Should have** | Role-Based Access Control (RBAC) | **Done** | 5 roles configured via Django groups; enforced via `core/permissions.py` decorators and helpers. |
| **Should have** | Mobile-Friendly Web UI | **Done** | Clean, responsive viewport HTML templates for sales reps (`/orders/new/`, `/my-sales/`). |
| **Stretch** | Owner Executive Dashboard | **Not done** | `view_dashboard` permission exists in `permissions.py`, but no dedicated dashboard route or view exists in `urls.py`. |
| **Stretch** | Multi-product Overhead Allocation | **Not done** | Overheads attach directly to a single production run rather than amortizing across multiple concurrent runs. |
| **Stretch** | Partial Dispatch & Backorders | **Not done** | Verified in `core/services.py`: dispatches must cover the full invoice quantity; no partial dispatches exist. |
| **Stretch** | Purchase Returns to Vendors | **Not done** | Verified in `core/services.py`: no purchase return service or model exists. |
| **Stretch** | Offline Sales Order Sync | **Not done** | Web-based online workflow only. |
| **Stretch** | Batch Number & Expiry Date Tracking | **Not done** | Inventory tracked by item aggregated balance; no shelf-life or expiry dates. |
| **Stretch** | Salesman GPS Location Check-in | **Not done** | No geolocation capture on order submission. |
| **Stretch** | Tally XML / JSON Accounting Export | **Not done** | System exports standard CSV reports only. |
| **Stretch** | Multilingual / Tamil UI Localization | **Not done** | Interface is English only. |
| **Stretch** | External REST API | **Not done** | Server-rendered Django views only; no exposed API endpoints in `urls.py`. |

---

## 6. Benchmark Results and Explanations

The benchmark simulates a standard operating cycle: opening inventory, a purchase of 60 kg flour, a production batch of Murukku 200 g with scrap tracking, overhead logging, order placement for Retailer A and Distributor B, invoicing, dispatch, and partial payment.

### Expected vs Actual Benchmark Results

| Parameter / Milestone | Target / Expected | Actual System Result | Status | Detailed Explanation |
|---|---|---|---|---|
| **Flour Closing Balance** | 120 kg (120,000 g) | 120,000 g | **Match** | Opening 100 kg + Purchase 60 kg - Production Issue 40 kg = 120 kg remaining. |
| **Cooking Oil Balance** | 19.5 L (19,500 ml) | 19,500 ml | **Match** | Opening 30 L - Production Issue 10.5 L = 19.5 L remaining. |
| **200 g Pouch Balance** | 306 pcs | 306 pcs | **Match** | Opening 500 pcs - Total Consumed 194 pcs (188 good + 6 scrap) = 306 pcs remaining. |
| **Carton Balance** | 21 pcs | 21 pcs | **Match** | Opening 30 pcs - Production Used 9 pcs = 21 pcs remaining. |
| **Murukku 200 g Output** | 238 pcs | 238 pcs | **Match** | Opening 50 pcs + Production Run Output 188 pcs = 238 pcs available. |
| **Murukku post-dispatch** | 88 pcs | 88 pcs | **Match** | 238 pcs available - 150 pcs dispatched to Retailer A = 88 pcs remaining. |
| **Yield Variance** | -6.00% (-600 bp) | -6.00% | **Flagged** | Expected 200 pkts, actual 188 pkts: `((188 - 200) / 200) * 10000 = -600 bp`. Exceeds 3.00% tolerance (`\|-600\| > 300`). |
| **Cooking Oil Variance** | +5.00% (+500 bp) | +5.00% | **Flagged** | Standard 10,000 ml, actual 10,500 ml: `((10500 - 10000) / 10000) * 10000 = +500 bp`. Exceeds 3.00% tolerance (`\|+500\| > 300`). |
| **Pouch Variance** | -3.00% (-300 bp) | -3.00% | **Not Flagged** | Standard 200 pcs, actual 194 pcs: `((194 - 200) / 200) * 10000 = -300 bp`. **Not flagged because the rule is strictly greater than 3% tolerance (`\|-300\| > 300` evaluates to False).** |
| **Retailer A Invoice Total** | Rs 7,875.00 | Rs 7,875.00 | **Match** | Taxable Rs 7,500.00 (150 pkts @ Rs 50) + CGST 2.5% (Rs 187.50) + SGST 2.5% (Rs 187.50) = Rs 7,875.00 (Intra-state, TN code 33). |
| **Retailer A Outstanding** | Rs 3,875.00 | Rs 3,875.00 | **Match** | Invoice Total Rs 7,875.00 - UPI Payment Rs 4,000.00 = Rs 3,875.00. |
| **Distributor B Order Status** | Blocked (Shortage 32) | Blocked (Shortage 32) | **Match** | Ordered 120 pkts, only 88 pkts available. Approval blocked with shortage warning. Customer in Karnataka (code 29; IGST applies). |
| **Cost per Packet** | Rs 31.14 | Rs 31.14 | **Match** | Total cost Rs 5,855.00 divided across 188 good packets = 3,114.36 paise -> rounded half up to 3,114 paise (Rs 31.14). |
| **Gross Margin %** | 37.72% | 37.72% | **Match** | Selling price Rs 50.00 - Unit cost Rs 31.14 = Margin Rs 18.86. Margin %: `(18.86 / 50.00) * 100 = 37.72%` (3,772 bp). |

---

### Step-by-Step Costing Breakdown

```text
1. Weighted-Average Flour Cost Calculation:
   - Opening Flour Stock:     100 kg @ Rs 42.00/kg  =  100,000 g  valued at  420,000 paise (Rs 4,200.00)
   - Inward Purchase:          60 kg @ Rs 45.00/kg  =   60,000 g  valued at  270,000 paise (Rs 2,700.00)
   -------------------------------------------------------------------------------------------------------
   - Total Pool Before Run:   160 kg                =  160,000 g  valued at  690,000 paise (Rs 6,900.00)
   - Weighted-Average Rate:   690,000 paise / 160 kg = 4,312.5 paise/kg (Rs 43.125 / kg)

2. Raw Materials Cost (Issued to Run):
   - Flour:                   40 kg @ Rs 43.125/kg  =  172,500 paise (Rs 1,725.00)
   - Cooking Oil:             10.5 L @ Rs 140.00/L  =  147,000 paise (Rs 1,470.00) [from opening 30 L pool]
   - Salt & Spice Mix:        1.2 kg @ Rs 200.00/kg =   24,000 paise   (Rs 240.00) [from opening 5 kg pool]
   -------------------------------------------------------------------------------------------------------
   - Subtotal Raw Materials:                            343,500 paise (Rs 3,435.00)

3. Packaging Materials Cost (Actual Consumption):
   - 200 g Pouches:           194 pcs @ Rs 2.50 ea  =   48,500 paise   (Rs 485.00) [188 good + 6 damaged]
   - Cartons:                 9 pcs @ Rs 15.00 ea   =   13,500 paise   (Rs 135.00)
   -------------------------------------------------------------------------------------------------------
   - Subtotal Packaging:                                 62,000 paise   (Rs 620.00)

4. Total Material Cost:
   - Raw Materials + Packaging = 343,500 + 62,000    =  405,500 paise (Rs 4,055.00)

5. Overhead Expenses Attached to Run:
   - Direct Labour Expense:                            120,000 paise (Rs 1,200.00)
   - Gas & Power Expense:                               60,000 paise   (Rs 600.00)
   -------------------------------------------------------------------------------------------------------
   - Subtotal Overheads:                               180,000 paise (Rs 1,800.00)

6. Total Batch Production Cost:
   - Materials (Rs 4,055.00) + Overheads (Rs 1,800.00) = 585,500 paise (Rs 5,855.00)

7. Unit Cost and Profit Margin:
   - Actual Good Output:       188 packets
   - Cost per Packet:          585,500 paise / 188 pkts = 3,114.3617... paise
                               Rounded (ROUND_HALF_UP)  = 3,114 paise (Rs 31.14 per packet)
   - Catalog Selling Price:    Rs 50.00 (5,000 paise)
   - Margin per Packet:        5,000 - 3,114 = 1,886 paise (Rs 18.86)
   - Gross Margin %:           (1,886 * 10,000) / 5,000 = 3,772 basis points = 37.72%
```

---

## 7. How to Run the Benchmark Manually in the Web UI

Follow these 9 sequential steps in your browser:

> **Important Operational Notes:**
> 1. The inward purchase (Step 1) **must be recorded before** the production run (Step 2) because stock issuance computes cost using the weighted-average inventory valuation active at that precise instant.
> 2. On the production screen (`/production/new/`), raw material issues, good output, scrap pouches, and cartons are entered on the **exact same form submission**.

* **Step 1: Record Raw Material Purchase**
  * Login as `production1` (or `admin1`).
  * Navigate to **`/purchase/new/`**.
  * Select Supplier `Sri Murugan Traders`, Item `Flour`, Quantity `60`, Unit `kg`, Rate `45.00`, and Invoice No `INV-BM-01`.
  * Click **Record Purchase**. Flour stock increases to 160 kg with a weighted-average cost of Rs 43.125/kg.
* **Step 2: Record Batch Production Run**
  * As `production1`, navigate to **`/production/new/`**.
  * Select Product `Murukku 200 g`.
  * Enter Material Issues: Flour `40`, Cooking oil `10.5`, Salt and spice mix `1.2`.
  * Enter Output & Packaging: Actual good packets `188`, Damaged pouches `6`, Cartons used `9`.
  * Click **Record Production Run**. You will be redirected to `/production/1/`. Notice yield variance (-6.00%) and oil variance (+5.00%) are highlighted in red flags; pouches (-3.00%) is unflagged.
* **Step 3: Attach Overhead Expenses**
  * On page **`/production/1/`**, scroll to the **Record Expense** form.
  * Select `Labour`, enter Amount `1200.00`, Note `Shift labour`, click **Add Expense**.
  * Select `Gas and power`, enter Amount `600.00`, Note `Frying fuel`, click **Add Expense**.
  * The Costing Summary updates immediately to: Material Cost Rs 4,055.00, Overheads Rs 1,800.00, Total Cost Rs 5,855.00, Cost per Packet **Rs 31.14**, Margin **37.72%**.
* **Step 4: Place Sales Order for Retailer A**
  * Logout and log in as `salesman_a`.
  * Navigate to **`/orders/new/`**.
  * Select Customer `Retailer A`, enter Quantity `150` for `Murukku 200 g`, and click **Place Order**. Order #1 is created in `SUBMITTED` status.
* **Step 5: Approve Order for Retailer A**
  * Logout and log in as `admin1`.
  * Navigate to **`/orders/1/`**.
  * Review the Stock Check table (150 ordered, 238 available, 0 shortage). Click **Approve Order**. Status transitions to `APPROVED`.
* **Step 6: Generate GST Tax Invoice**
  * On **`/orders/1/`**, click **Create Invoice**.
  * Invoice `INV/2026-27/0001` is issued for Rs 7,875.00 (Taxable Rs 7,500.00 + CGST Rs 187.50 + SGST Rs 187.50). Status transitions to `INVOICED`.
* **Step 7: Record Dispatch**
  * On **`/orders/1/`**, click **Record Dispatch**.
  * 150 packets are deducted from inventory in `StockLedger`. Status changes to `DISPATCHED`. Murukku stock drops from 238 to 88 packets.
* **Step 8: Place Order for Distributor B (Demonstrating Stock Block)**
  * Logout and log in as `salesman_a`.
  * Navigate to **`/orders/new/`**.
  * Select Customer `Distributor B`, enter Quantity `120` for `Murukku 200 g`, click **Place Order**. Order #2 is created.
* **Step 9: Attempt Approval and Record Payment**
  * Logout and log in as `admin1`.
  * Navigate to **`/orders/2/`**.
  * Review the Stock Check table: ordered 120, available 88, shortage 32 packets.
  * Click **Approve Order**. The system halts the operation and displays an error banner: `Insufficient stock: Murukku 200 g short by 32`.
  * Navigate to **`/invoices/1/`** (Retailer A's invoice). In the Record Payment form, enter Amount `4000.00`, Mode `UPI`, Reference `UPI-RET-A-001`, click **Record Payment**.
  * The invoice shows Total Paid Rs 4,000.00, Outstanding **Rs 3,875.00**. Click **Download PDF** to inspect the generated tax invoice.

---

## 8. Architectural Assumptions

1. **Storage & Units:** Implemented on SQLite. All monetary values are strictly stored as integer paise (Rs 1.00 = 100 paise; Rs 45.00 = 4,500 paise). All inventory quantities are stored as integers in immutable base units (grams `g`, millilitres `ml`, pieces `pcs`). Floats are strictly prohibited in calculation and storage to eliminate floating-point rounding errors.
2. **Rounding Rule:** Python `Decimal` with `ROUND_HALF_UP` is enforced across all division calculations (basis points, taxes, unit costs, and margins).
3. **Inventory Valuation:** Moving weighted-average cost is used for material issues and inventory valuation. Opening stock of 50 finished packets of Murukku 200 g is valued at 0 paise (opening production cost was unspecified).
4. **Overhead Allocation:** Direct overheads (labour, power) attach directly to the specific production run where they were incurred and are absorbed entirely across the **good packets** produced in that run.
5. **GST Rules & Entity State:** Factory is located in Tamil Nadu (`state_code = "33"`). Intra-state sales (Tamil Nadu, state code 33) split GST evenly into CGST (50%) and SGST (50%). Inter-state sales (Karnataka, state code 29) incur 100% IGST. Test GSTINs are dummy placeholders; HSN 2106 (edible preparations / snacks) is assumed for Murukku 200 g.
6. **Stock Movement Trigger:** Finished goods inventory is deducted **strictly upon physical dispatch**, not upon order placement, approval, or invoice generation.
7. **Packaging vs Raw Material Consumption:** Packaging items (pouches and cartons) are deducted based on **actual usage** (e.g., 194 pouches = 188 good packets + 6 damaged pouches; 9 cartons). Raw materials (flour, oil, spices) are deducted based on the actual quantity issued into production.
8. **Invoicing & Cancellation Integrity:** An invoice with recorded payments cannot be cancelled. Cancelling an unpaid invoice automatically generates a formal Credit Note, reverses associated stock dispatch rows, and sets the sales order to `CANCELLED`.
9. **UI & Access Scopes:** Sales representatives access a mobile-optimized web interface instead of a native application. The `admin` role manages operational orders and invoices but has no permission to access the management reporting suite (`/reports/`), which is reserved for `accountant` and `owner`.

---

## 9. Known Technical Gaps and Limitations

* **Order Race Condition:** Two concurrently approved orders can compete for the same finished goods inventory. The system prevents negative stock at the moment of dispatch (re-checking balance), but order approval does not create a provisional stock reservation.
* **Reporting Output:** All 10 reports in `/reports/` are downloadable as CSV only; there is no PDF report export (PDF export is implemented for individual tax invoices only).
* **Database Trigger Protections:** Ledger immutability is enforced via Django model `save()` and `delete()` method overrides. Direct raw SQL execution in SQLite would bypass Python-layer immutability; in an enterprise production environment, database-level triggers or restricted role grants would be required.
* **Overhead Spanning:** Overheads cannot be amortized or apportioned across multiple concurrent product batches or runs.
* **Owner Dashboard:** While `view_dashboard` is defined in `core/permissions.py`, no executive dashboard page or URL route currently exists in `core/urls.py`.
* **Missing Operational Features:** Verified in `core/services.py`: there is **no partial dispatch** mechanism (orders must be dispatched in full) and **no purchase return** workflow. The system does not support offline order synchronization, batch/lot expiry tracking, salesman GPS tracking, Tally XML integration, or multilingual/Tamil interfaces.

---

## 10. Automated Test Suite

### Running the Tests
To run the automated test suite, navigate to the `backend/` directory and execute:

```bash
python manage.py test core
```

### Test Suite Coverage
The application includes comprehensive unit and integration tests across 8 test suites:

1. **`core.tests` (`StockFoundationTests`, `ServiceLayerTests`, `ReportTests`, `PDFTests`):** Validates base unit conversions, weighted-average issue valuation, ledger immutability constraints, order approval and rejection rules, GST tax calculations, invoice and credit note generation, payment tracking, CSV report generators, and ReportLab PDF layout.
2. **`core.tests_permissions`:** Exhaustively tests the RBAC matrix across all 5 roles (`salesman`, `admin`, `production`, `accountant`, `owner`), ensuring strict separation between state-changing and read-only actions.
3. **`core.tests_web`:** Tests mobile-friendly salesman views (order creation form validation, row-level isolation, and personal sales aggregations).
4. **`core.tests_web_admin`:** Tests administrative actions (order line updates, approvals, rejections, tax invoicing, dispatch execution, payment logging, and credit note cancellations).
5. **`core.tests_web_production`:** Tests production workflows (inward purchase logging, production run creation, raw material issues, and scrap pouch/carton tracking).
6. **`core.tests_web_costing`:** Tests overhead expense logging, material vs overhead expense breakdown, unit packet costing, and gross margin calculations.
7. **`core.tests_web_reports`:** Verifies access control, date range filters, and CSV downloads across all 10 reports.
8. **`core.tests_seed_supplier`:** Validates idempotency of baseline benchmark seed scripts.

---
*Authored as part of the NIT Trichy CDI Winter Internship 2026 submission by Suriya.*
