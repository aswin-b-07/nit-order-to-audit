# Rules for the AI agent (apply to every task)

Project: Order-to-Audit system for a small food factory (NIT Trichy internship task).
Stack: Django + Django REST Framework + SQLite. Folder: backend/. App: core.

Hard rules:
1. Do ONLY the task I ask for. No extra features, models, or files.
2. Money is stored as integer paise (Rs 45 = 4500). Never use float for money or stock.
3. Quantities are integers in base units: grams (g), millilitres (ml), pieces (pcs).
   Example: 40 kg = 40000 g, 10.5 L = 10500 ml. Convert only with Python Decimal, then to int.
4. Stock is never overwritten. Every change is a new StockLedger row. Balance = sum of quantity.
5. Every ledger row records its source (source_type, source_id) for traceability.
6. Keep code simple and readable, with short comments. No clever tricks.
7. Do not install new packages without asking me.
8. Do not start the server. Do not run git commands. I will commit myself.
9. When finished: run the tests, show me the output, list the files you changed,
   and explain each change in simple words.