# Order-to-Audit System for a Small Food Manufacturer

---

## 1. Project Overview

A working prototype of an order-to-audit system for a small snack manufacturer: sales orders, GST invoicing, BOM-based production with variance flagging, ledger-based inventory, costing, and audit reports. Individual submission by Aswin for the NIT Trichy CDI Winter Internship 2026.

- **Demo video:** (link will be added after upload)
- **Live demo:** https://nit-order-to-audit-2e69.onrender.com (free hosting: the first load after idle takes up to a minute, and data resets when the service restarts. Test logins are in section 3.)
- **Design note:** [docs/DESIGN_NOTE.md](docs/DESIGN_NOTE.md)
- **Sample invoice PDF:** [docs/sample_invoice_retailer_a.pdf](docs/sample_invoice_retailer_a.pdf)

---

## 2. Setup and Run Guide

Requires Python 3.12. All dependencies are pinned in `backend/requirements.txt`:

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

Then open **http://127.0.0.1:8000/login/** and sign in with one of the test accounts in section 3.

> **Windows note:** if PowerShell says "running scripts is disabled", run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then activate the environment again.
>
> **Benchmark note:** skip step 5 (`run_benchmark`) if you want to follow the manual walkthrough in section 7. That command performs the benchmark transactions in advance, so use it on a fresh database only to see the finished result.
>
> **Resetting the database:** stop the server, delete `backend/db.sqlite3`, then repeat steps 3 and 4.

### Single Run Commands

**Windows PowerShell (single command):**
```powershell
python -m venv venv; .\venv\Scripts\Activate.ps1; pip install -r backend\requirements.txt; cd backend; python manage.py migrate; python manage.py seed_benchmark; python manage.py seed_users; python manage.py runserver
```

**Linux / macOS (single command):**
```bash
python3 -m venv venv && source venv/bin/activate && pip install -r backend/requirements.txt && cd backend && python manage.py migrate && python manage.py seed_benchmark && python manage.py seed_users && python manage.py runserver
```

---

## 3. Test Logins and Role Permissions

All seeded test accounts (dummy data only) share the password: **`Test@1234`**

| Username | Role | Permitted Actions & Responsibilities |
|---|---|---|
| `salesman_a` | **Salesman** | Place new customer sales orders (`/orders/new/`), view own submitted orders (`/orders/`), view customer directory, view product price list, view own sales summary (`/my-sales/`). Cannot approve orders, dispatch goods, manage invoices, record production, or view reports. |
| `salesman_b` | **Salesman** | Same capabilities as `salesman_a`. Enforces strict row-level isolation: cannot see orders submitted by `salesman_a`. |
| `admin1` | **Admin** | Order-to-cash administration: view all orders across all sales reps (`/orders/`), edit lines/prices on submitted orders, approve orders (with automated stock verification), reject orders with reason, generate GST tax invoices (`/invoices/`), record stock dispatches, log customer payments, cancel invoices via credit note, download invoice PDFs, view customer directory and pricing. **Does not have access to accounting reports or production screens.** |
| `production1` | **Production** | Record incoming raw material and packaging purchases (`/purchase/new/`), execute batch production runs against the BOM (`/production/new/`), record overhead expenses (labour, power) on runs (`/production/<id>/`), inspect real-time inventory balances, analyze unit costing and margins. Cannot place orders, approve sales, or create invoices. |
| `accountant1` | **Accountant** | Financial and compliance reporting: access the reporting suite (`/reports/`), download all 10 CSV reports, view all orders, view inventory balances and ledger, view batch costing, download invoice PDFs. **Strictly read-only:** cannot create or modify orders, invoices, dispatches, purchases, or runs. |
| `owner1` | **Owner** | Executive visibility: owner dashboard (`/dashboard/`: today's orders, stock levels, variance alerts, cost per packet, outstanding dues), reports suite (`/reports/`), all orders, stock balances, production runs and batch costing, invoice PDFs. |

---

## 4. Technology Stack and Rationale

The solution is implemented using **Django 6 + SQLite** with server-rendered mobile-responsive HTML templates and **ReportLab** for GST-compliant PDF invoice generation. This stack was selected because a unified Django application guarantees strict ACID database transactions, relational integrity, and zero-setup deployment on SQLite without introducing the latency or maintenance overhead of external database servers or microservices. Server-rendered responsive templates eliminate frontend compilation build steps while providing a reliable interface for field sales representatives on mobile devices and operators on the factory floor.

A native mobile app (Flutter) was started and then dropped to protect time for correctness. Field salesmen use the mobile-friendly web pages instead, which the brief allows ("web, mobile (native or PWA)").

---

## 5. Feature Matrix Against PDF Tiers

### Must have

| Feature | Status | Implementation Details |
|---|---|---|
| Login with at least three roles | **Done** | 5 roles (salesman, admin, production, accountant, owner) configured via Django groups; enforced by `core/permissions.py` decorators and helpers on every view. |
| Product, customer, raw-material and BOM masters | **Done** | `Item`, `Customer`, `ProductPrice`, `BOM` and `BOMLine` models, editable in the Django admin (`/admin/`). |
| Mobile-friendly order entry and admin approval | **Done** | Salesman order page (`/orders/new/`) with prices from the price list; admin approves or rejects with a stock check (`/orders/<id>/`). |
| GST invoice as PDF | **Done** | Built with ReportLab (`build_invoice_pdf`), downloadable at `/invoices/<id>/pdf/`. CGST+SGST or IGST by place of supply; numbering by financial year (`INV/2026-27/0001`). |
| Production run updates raw-material and finished-goods stock | **Done** | `record_production_run` writes ledger rows for every issue and for the finished output in one atomic transaction. |
| Planned vs actual consumption and yield, with variance shown | **Done** | `ProductionRun` and `ProductionRunLine` store basis-point variance; flagged when strictly greater than the 3% tolerance (configurable). |
| Stock summary and stock ledger | **Done** | Append-only `StockLedger` with the source document on every movement; stock pages and CSV reports. |
| Sales register and stock report, exportable to CSV | **Done** | Reports suite at `/reports/` (see Should have for the full list). |

### Should have

| Feature | Status | Implementation Details |
|---|---|---|
| Purchases and supplier records | **Done** | `Supplier` and `Purchase` models, `record_purchase` service, web form at `/purchase/new/`. |
| Payments, outstanding and ageing | **Done** | Payment mode, date and reference; invoice-level and customer-level outstanding in integer paise; outstanding report with ageing. |
| Cost per packet with overheads | **Done** | Labour and gas/power expenses logged against a run; total cost, cost per good packet and margin shown on the run page. |
| GST summary report | **Done** | Outward-supply GST summary with CGST, SGST and IGST totals in the reports suite. |
| Variance alerts beyond tolerance | **Done** | Red flags on the production run page and a variance alerts card on the owner dashboard. |
| Owner dashboard | **Done** | `/dashboard/`: today's orders, stock levels, variance alerts, latest cost per packet, outstanding dues. |
| Reports suite | **Done** | 10 reports with date-range filtering and CSV export: sales register, customer-wise sales, product-wise sales, GST summary, purchase register, stock summary, stock ledger, production and wastage, payments and collections, outstanding with ageing. |

### Additional features built

| Feature | Status | Implementation Details |
|---|---|---|
| Credit note and invoice cancellation | **Done** | `CreditNote` cancels an issued invoice and reverses the stock dispatch with `SALE_RETURN` ledger rows; blocked if payments exist. Invoices are never deleted. |
| Append-only audit trail | **Done** | `StockLedger` and `AuditLog` refuse edits and deletes at the model layer; every state-changing action writes an audit row in the same transaction. |

### Stretch goals (from the brief)

| Stretch goal | Status |
|---|---|
| Offline order capture with sync | Not done |
| Batch and expiry tracking | Not done |
| Reorder levels and low-stock alerts | Not done |
| Production planning from pending orders | Not done |
| Salesman route or visit log with GPS | Not done |
| Tally-compatible export | Not done |
| e-Invoice / e-Way Bill data format | Not done |
| Tamil language interface | Not done |

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
| **Pouch Variance** | -3.00% (-300 bp) | -3.00% | **Not Flagged** | Standard 200 pcs, actual 194 pcs: `((194 - 200) / 200) * 10000 = -300 bp`. **Not flagged because the rule is strictly greater than the 3% tolerance (`\|-300\| > 300` evaluates to False).** |
| **Retailer A Invoice Total** | Rs 7,875.00 | Rs 7,875.00 | **Match** | Taxable Rs 7,500.00 (150 pkts @ Rs 50) + CGST 2.5% (Rs 187.50) + SGST 2.5% (Rs 187.50) = Rs 7,875.00 (intra-state, TN code 33). |
| **Retailer A Outstanding** | Rs 3,875.00 | Rs 3,875.00 | **Match** | Invoice Total Rs 7,875.00 - UPI Payment Rs 4,000.00 = Rs 3,875.00. |
| **Distributor B Order Status** | Blocked (Shortage 32) | Blocked (Shortage 32) | **Match** | Ordered 120 pkts, only 88 pkts available. Approval blocked with shortage warning. Customer in Karnataka (code 29; IGST applies if invoiced). |
| **Cost per Packet** | Rs 31.14 | Rs 31.14 | **Match** | Total cost Rs 5,855.00 divided across 188 good packets = 3,114.36 paise, rounded half up to 3,114 paise (Rs 31.14). |
| **Gross Margin %** | 37.72% | 37.72% | **Match** | Selling price Rs 50.00 - Unit cost Rs 31.14 = Margin Rs 18.86. Margin %: `(18.86 / 50.00) * 100 = 37.72%` (3,772 bp). |

**Differences from the brief:** none in the checked results. The brief leaves the cost per packet to the candidate (valuation method and overhead rule); ours is weighted average with overheads absorbed by the good packets of the run, as shown below. The brief's pouch count (306 = 194 consumed) is reproduced by consuming packaging on actual use, not at the BOM standard of 200.

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

Use a **fresh database** (setup steps 1 to 4, without `run_benchmark`). Then follow these steps in your browser:

> **Important Operational Notes:**
> 1. The inward purchase (Step 1) **must be recorded before** the production run (Step 2) because stock issuance computes cost using the weighted-average inventory valuation active at that precise instant.
> 2. On the production screen (`/production/new/`), raw material issues, good output, scrap pouches, and cartons are entered on the **exact same form submission**.
> 3. Order numbers and invoice numbers below assume a fresh database.

* **Step 1: Record Raw Material Purchase**
  * Log in as `production1`.
  * Navigate to **`/purchase/new/`**.
  * Select Supplier `Sri Murugan Traders`, Item `Flour`, Quantity `60`, Unit `kg`, Rate `45.00`, and Invoice No `INV-BM-01`.
  * Click **Record Purchase**. Flour stock increases to 160 kg with a weighted-average cost of Rs 43.125/kg.
* **Step 2: Record Batch Production Run**
  * As `production1`, navigate to **`/production/new/`**.
  * Select Product `Murukku 200 g`.
  * Enter Material Issues: Flour `40`, Cooking oil `10.5`, Salt and spice mix `1.2`.
  * Enter Output and Packaging: Actual good packets `188`, Damaged pouches `6`, Cartons used `9`.
  * Click **Record Production Run**. You will be redirected to `/production/1/`. Notice yield variance (-6.00%) and oil variance (+5.00%) are flagged in red; pouches (-3.00%) is not flagged.
* **Step 3: Attach Overhead Expenses**
  * On page **`/production/1/`**, scroll to the **Record Expense** form.
  * Select `Labour`, enter Amount `1200.00`, Note `Shift labour`, click **Add Expense**.
  * Select `Gas and power`, enter Amount `600.00`, Note `Frying fuel`, click **Add Expense**.
  * The Costing Summary updates to: Material Cost Rs 4,055.00, Overheads Rs 1,800.00, Total Cost Rs 5,855.00, Cost per Packet **Rs 31.14**, Margin **37.72%**.
* **Step 4: Place Sales Order for Retailer A**
  * Log out and log in as `salesman_a` (use a phone-sized browser window to see the mobile view).
  * Navigate to **`/orders/new/`**.
  * Select Customer `Retailer A`, enter Quantity `150` for `Murukku 200 g`, and click **Place Order**. Order #1 is created in `SUBMITTED` status.
* **Step 5: Approve Order for Retailer A**
  * Log out and log in as `admin1`.
  * Navigate to **`/orders/1/`**.
  * Review the Stock Check table (150 ordered, 238 available, 0 shortage). Click **Approve Order**. Status becomes `APPROVED`.
* **Step 6: Generate GST Tax Invoice**
  * On **`/orders/1/`**, click **Create Invoice**.
  * Invoice `INV/2026-27/0001` is issued for Rs 7,875.00 (Taxable Rs 7,500.00 + CGST Rs 187.50 + SGST Rs 187.50). Status becomes `INVOICED`.
* **Step 7: Record Dispatch**
  * On **`/orders/1/`**, click **Record Dispatch**.
  * 150 packets are deducted from inventory in `StockLedger`. Status becomes `DISPATCHED`. Murukku stock drops from 238 to 88 packets.
* **Step 8: Place Order for Distributor B (Demonstrating Stock Block)**
  * Log out and log in as `salesman_b`.
  * Navigate to **`/orders/new/`**.
  * Select Customer `Distributor B`, enter Quantity `120` for `Murukku 200 g`, click **Place Order**. Order #2 is created.
* **Step 9: Attempt Approval and Record Payment**
  * Log out and log in as `admin1`.
  * Navigate to **`/orders/2/`**.
  * Review the Stock Check table: ordered 120, available 88, shortage 32 packets.
  * Click **Approve Order**. The system halts the operation and shows an error banner: `Insufficient stock: Murukku 200 g short by 32`. (Distributor B is in Karnataka, so IGST would apply if invoiced.)
  * Navigate to **`/invoices/1/`** (Retailer A's invoice). In the Record Payment form, enter Amount `4000.00`, Mode `UPI`, Reference `UPI-RET-A-001`, click **Record Payment**.
  * The invoice shows Total Paid Rs 4,000.00, Outstanding **Rs 3,875.00**. Click **Download PDF** to inspect the generated tax invoice.
* **Step 10: Reports and Dashboard**
  * Log in as `accountant1`, open **`/reports/`**, choose a date range and download any report as CSV (for example the sales register and the stock ledger).
  * Log in as `owner1` and open **`/dashboard/`** to see today's orders, stock levels, variance alerts, cost per packet and outstanding dues.

---

## 8. Architectural Assumptions

1. **Storage and Units:** Implemented on SQLite. All monetary values are stored as integer paise (Rs 1.00 = 100 paise; Rs 45.00 = 4,500 paise). All inventory quantities are stored as integers in base units (grams `g`, millilitres `ml`, pieces `pcs`). Floats are not used in calculation or storage, which avoids floating-point rounding errors.
2. **Rounding Rule:** Python `Decimal` with `ROUND_HALF_UP` is used for all division calculations (basis points, taxes, unit costs, and margins).
3. **Inventory Valuation:** Moving weighted-average cost is used for material issues and inventory valuation. Opening stock of 50 finished packets of Murukku 200 g is valued at 0 paise (opening production cost was not given).
4. **Overhead Allocation:** Direct overheads (labour, power) attach to the specific production run where they were incurred and are absorbed entirely by the **good packets** of that run.
5. **GST Rules and Entity State:** The factory is in Tamil Nadu (`state_code = "33"`). Intra-state sales (state code 33) split GST evenly into CGST and SGST. Inter-state sales (for example Karnataka, state code 29) incur IGST. GSTINs, the factory address and names are dummy placeholders; HSN 2106 (edible preparations) is assumed for Murukku 200 g.
6. **Stock Movement Trigger:** Finished goods are deducted **only at dispatch**, not at order placement, approval, or invoice generation.
7. **Packaging vs Raw Material Consumption:** Packaging items (pouches and cartons) are deducted on **actual usage** (194 pouches = 188 good packets + 6 damaged; 9 cartons). Raw materials (flour, oil, spices) are deducted on the actual quantity issued into production.
8. **Invoicing and Cancellation Integrity:** An invoice with recorded payments cannot be cancelled. Cancelling an unpaid invoice generates a credit note, reverses the dispatch ledger rows, and sets the order to `CANCELLED`.
9. **Variance Rule:** A line or the yield is flagged only when its variance is strictly greater than the tolerance (3% by default, set in `settings.py`).
10. **UI and Access Scopes:** Salesmen use a mobile-friendly web interface instead of a native application. The `admin` role manages orders and invoices but cannot open the reports suite (`/reports/`), which is for `accountant` and `owner`.

---

## 9. Known Technical Gaps and Limitations

* **No native mobile app and no hosted demo by default:** the salesman side is a mobile-friendly web page; the app runs locally with the commands in section 2.
* **Order race condition:** two approved orders can compete for the same finished goods. The system never lets stock go negative, because dispatch re-checks the balance, but approval does not reserve stock.
* **Reports are CSV only:** there is no PDF export for reports (PDF is implemented for individual tax invoices only).
* **Ledger protection is in the application layer:** immutability is enforced by Django model `save()` and `delete()` overrides. Raw SQL on the SQLite file would bypass it; a production system would add database triggers or restricted database roles.
* **Overheads are not apportioned:** they attach to a single production run and cannot be split across concurrent batches or products.
* **No partial dispatch and no purchase returns:** orders are dispatched in full, and there is no vendor-return workflow.
* **No external REST API:** the application is server-rendered only.
* **Not built:** offline order synchronization, batch and expiry tracking, reorder alerts, production planning, GPS visit logs, Tally export, e-Invoice mock, Tamil interface.

---

## 10. Automated Test Suite

### Running the Tests
Navigate to the `backend/` directory and execute:

```bash
python manage.py test core
```

The full run takes a few minutes.

### Test Suite Coverage
The tests are spread over 9 test modules:

1. **`core.tests`:** unit conversions, weighted-average issue valuation, ledger immutability, order approval and rejection rules, GST calculations, invoice and credit note generation, payment tracking, CSV report generators, and PDF invoice output.
2. **`core.tests_permissions`:** the role-based access matrix across all 5 roles, separating state-changing from read-only actions.
3. **`core.tests_web`:** salesman views (order form validation, row-level isolation, personal sales summary).
4. **`core.tests_web_admin`:** admin actions (line edits, approvals, rejections, invoicing, dispatch, payments, credit-note cancellations).
5. **`core.tests_web_production`:** purchases, production runs, material issues, and scrap pouch/carton tracking.
6. **`core.tests_web_costing`:** overhead expenses, cost breakdown, cost per packet, and margin.
7. **`core.tests_web_reports`:** access control, date-range filters, and CSV downloads across all 10 reports.
8. **`core.tests_web_dashboard`:** owner dashboard access, figures, and empty state.
9. **`core.tests_seed_supplier`:** idempotency of the baseline seed scripts.

---
*Authored as part of the NIT Trichy CDI Winter Internship 2026 submission by Aswin.*