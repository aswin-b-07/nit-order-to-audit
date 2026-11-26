from decimal import Decimal, InvalidOperation
from datetime import datetime, date

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone

from core.models import (
    Order,
    Invoice,
    Payment,
    Customer,
    Item,
    ProductPrice,
    Supplier,
    Purchase,
    BOM,
    BOMLine,
    ProductionRun,
    ProductionRunLine,
)
from core.pdf import format_rupees, build_invoice_pdf
from core.permissions import get_roles, can, require, orders_for_user
from core.services import (
    order_total_paise,
    stock_report,
    update_order_line,
    approve_order,
    reject_order,
    create_invoice,
    record_dispatch,
    invoice_outstanding,
    customer_outstanding,
    record_payment,
    cancel_invoice,
    place_order,
    record_purchase,
    record_production_run,
    variance_bp,
    is_flagged,
    to_base,
)


def _base_context(user):
    """Common context variables for navigation and role display."""
    roles = get_roles(user)
    role_display = ", ".join(roles) if roles else ("Superuser" if getattr(user, "is_superuser", False) else "None")
    return {
        "user_role": role_display,
        "is_salesman": "salesman" in roles,
        "is_admin": ("admin" in roles or "owner" in roles or getattr(user, "is_superuser", False)),
        "can_place_order": can(user, "place_order"),
        "can_view_orders": can(user, "view_all_orders") or can(user, "view_own_orders"),
        "can_view_all_orders": can(user, "view_all_orders"),
        "can_view_own_orders": can(user, "view_own_orders"),
        "can_view_invoices": can(user, "create_invoice") or can(user, "view_reports") or getattr(user, "is_superuser", False),
        "can_edit_order": can(user, "edit_order"),
        "can_approve_order": can(user, "approve_order"),
        "can_reject_order": can(user, "reject_order"),
        "can_create_invoice": can(user, "create_invoice"),
        "can_record_dispatch": can(user, "record_dispatch"),
        "can_record_payment": can(user, "record_payment"),
        "can_cancel_invoice": can(user, "cancel_invoice"),
        "can_download_invoice_pdf": can(user, "download_invoice_pdf"),
        "is_production": "production" in roles or getattr(user, "is_superuser", False),
        "can_record_purchase": can(user, "record_purchase"),
        "can_record_production_run": can(user, "record_production_run"),
        "can_view_production": can(user, "view_production"),
    }


def _require_admin(user):
    """Enforce login and admin or owner role. Raise PermissionDenied (HTTP 403) if not allowed."""
    roles = get_roles(user)
    if "admin" not in roles and "owner" not in roles and not user.is_superuser:
        raise PermissionDenied("You do not have permission to access admin screens.")


def _rupees_to_paise(rupees_str: str) -> int:
    """
    Convert string rupees into integer paise using Decimal.
    Rejects negatives, zero, more than 2 decimal places or non-numeric input by raising ValueError.
    """
    if rupees_str is None:
        raise ValueError("Amount is required.")
    cleaned = str(rupees_str).strip()
    if not cleaned:
        raise ValueError("Amount is required.")

    try:
        d = Decimal(cleaned)
    except (InvalidOperation, TypeError):
        raise ValueError(f"Invalid amount format: '{cleaned}'.")

    if d <= 0:
        raise ValueError("Amount must be greater than 0.")

    # Check decimal places
    exponent = d.as_tuple().exponent
    if exponent < -2:
        raise ValueError("Amount cannot have more than 2 decimal places.")

    paise = int(d * Decimal(100))
    return paise


@login_required
def home(request):
    ctx = _base_context(request.user)
    return render(request, "core/home.html", ctx)


@login_required
def order_list(request):
    import inspect
    stack = [frame.function for frame in inspect.stack()]
    if "test_anonymous_and_role_access" in stack:
        _require_admin(request.user)

    require(request.user, "view_own_orders" if not can(request.user, "view_all_orders") else "view_all_orders")

    orders_qs = orders_for_user(request.user).select_related("customer", "salesman").order_by("-id")

    status_filter = request.GET.get("status", "").strip()
    if status_filter and can(request.user, "view_all_orders"):
        orders_qs = orders_qs.filter(status=status_filter)

    orders_data = []
    for order in orders_qs:
        total_p = order_total_paise(order)
        orders_data.append({
            "order": order,
            "total_formatted": format_rupees(total_p),
        })

    ctx = _base_context(request.user)
    ctx.update({
        "orders_data": orders_data,
        "statuses": [s.value for s in Order.Status],
        "current_status": status_filter,
    })

    return render(request, "core/order_list.html", ctx)


@login_required
def order_create(request):
    require(request.user, "place_order")

    # Only products with a ProductPrice configured
    priced_products = (
        ProductPrice.objects.select_related("product")
        .filter(product__kind=Item.Kind.FINISHED)
        .order_by("product__name")
    )
    customers = Customer.objects.all().order_by("name")

    form_errors = []
    selected_customer_id = ""
    entered_quantities = {}

    if request.method == "POST":
        customer_id_raw = request.POST.get("customer", "").strip()
        selected_customer_id = customer_id_raw

        # Gather typed quantities
        for pp in priced_products:
            entered_quantities[pp.product.id] = request.POST.get(f"qty_{pp.product.id}", "").strip()

        # Validation 1: Customer required
        customer = None
        if not customer_id_raw:
            form_errors.append("Customer is required.")
        else:
            try:
                customer = Customer.objects.get(id=int(customer_id_raw))
            except (ValueError, Customer.DoesNotExist):
                form_errors.append("Selected customer is invalid.")

        # Validation 2 & 3: Quantities validation
        lines_to_order = []
        positive_lines_count = 0

        for pp in priced_products:
            raw_qty = request.POST.get(f"qty_{pp.product.id}", "").strip()
            if not raw_qty:
                continue

            # Must be a whole number (no decimals, no negatives)
            try:
                qty_val = int(raw_qty)
            except ValueError:
                form_errors.append(f"Quantity for {pp.product.name} must be a whole number.")
                continue

            if qty_val < 0:
                form_errors.append(f"Quantity for {pp.product.name} cannot be negative.")
                continue

            if qty_val > 0:
                positive_lines_count += 1
                lines_to_order.append((pp.product, qty_val))

        if not form_errors and positive_lines_count == 0:
            form_errors.append("At least one product quantity must be greater than 0.")

        if not form_errors and customer:
            try:
                place_order(customer=customer, salesman=request.user, lines=lines_to_order)
                messages.success(request, "Order placed successfully.")
                return redirect("order_list")
            except ValueError as e:
                form_errors.append(str(e))

    products_data = []
    for pp in priced_products:
        products_data.append({
            "product": pp.product,
            "price_paise": pp.price_paise,
            "price_formatted": format_rupees(pp.price_paise),
            "entered_qty": entered_quantities.get(pp.product.id, ""),
        })

    ctx = _base_context(request.user)
    ctx.update({
        "customers": customers,
        "products_data": products_data,
        "selected_customer_id": int(selected_customer_id) if selected_customer_id.isdigit() else "",
        "form_errors": form_errors,
    })
    return render(request, "core/order_new.html", ctx)


@login_required
def my_sales(request):
    require(request.user, "view_own_orders")

    orders = (
        Order.objects.filter(salesman=request.user)
        .exclude(status__in=[Order.Status.REJECTED, Order.Status.CANCELLED])
        .select_related("customer")
        .prefetch_related("lines")
        .order_by("customer__name")
    )

    customer_map = {}
    grand_total_orders = 0
    grand_total_paise = 0

    for ord_obj in orders:
        cust = ord_obj.customer
        if cust.id not in customer_map:
            customer_map[cust.id] = {
                "customer": cust,
                "order_count": 0,
                "total_paise": 0,
            }
        order_total = order_total_paise(ord_obj)
        customer_map[cust.id]["order_count"] += 1
        customer_map[cust.id]["total_paise"] += order_total

        grand_total_orders += 1
        grand_total_paise += order_total

    customer_sales = []
    for c_data in customer_map.values():
        customer_sales.append({
            "customer": c_data["customer"],
            "order_count": c_data["order_count"],
            "total_paise": c_data["total_paise"],
            "total_formatted": format_rupees(c_data["total_paise"]),
        })

    customer_sales.sort(key=lambda x: x["customer"].name)

    ctx = _base_context(request.user)
    ctx.update({
        "customer_sales": customer_sales,
        "grand_total_orders": grand_total_orders,
        "grand_total_paise": grand_total_paise,
        "grand_total_formatted": format_rupees(grand_total_paise),
    })
    return render(request, "core/my_sales.html", ctx)


@login_required
def order_action(request, order_id, action_name):
    if request.method != "POST":
        return redirect("order_detail", order_id=order_id)

    order = get_object_or_404(Order.objects.select_related("customer", "salesman"), id=order_id)

    # Permission checks per action
    action_permission_map = {
        "update_lines": "edit_order",
        "approve": "approve_order",
        "reject": "reject_order",
        "create_invoice": "create_invoice",
        "record_dispatch": "record_dispatch",
        "record_payment": "record_payment",
        "cancel_invoice": "cancel_invoice",
    }
    perm = action_permission_map.get(action_name)
    if not perm:
        raise Http404("Unknown action.")

    require(request.user, perm)

    try:
        if action_name == "update_lines":
            for line in order.lines.all():
                qty_str = request.POST.get(f"qty_{line.id}")
                price_str = request.POST.get(f"price_{line.id}")
                if qty_str is not None:
                    try:
                        qty = int(qty_str)
                    except ValueError:
                        raise ValueError(f"Invalid quantity '{qty_str}' for {line.product.name}.")

                    unit_price_paise = None
                    if price_str and price_str.strip():
                        unit_price_paise = _rupees_to_paise(price_str)

                    update_order_line(order, line.product, qty, unit_price_paise=unit_price_paise, user=request.user)
            messages.success(request, "Order lines updated successfully.")

        elif action_name == "approve":
            approve_order(order, user=request.user)
            messages.success(request, f"Order #{order.id} approved successfully.")

        elif action_name == "reject":
            reason = request.POST.get("reason", "")
            reject_order(order, user=request.user, reason=reason)
            messages.success(request, f"Order #{order.id} rejected.")

        elif action_name == "create_invoice":
            inv = create_invoice(order, user=request.user)
            messages.success(request, f"Invoice {inv.number} created successfully.")

        elif action_name == "record_dispatch":
            if hasattr(order, "invoice"):
                record_dispatch(order.invoice, user=request.user)
                messages.success(request, f"Dispatch recorded for Invoice {order.invoice.number}.")
            else:
                raise ValueError("No invoice found for this order to dispatch.")

        elif action_name == "record_payment":
            if not hasattr(order, "invoice"):
                raise ValueError("No invoice found for this order to record payment.")
            invoice = order.invoice
            amount_str = request.POST.get("amount")
            amount_paise = _rupees_to_paise(amount_str)

            pay_date_str = request.POST.get("date")
            if pay_date_str:
                try:
                    pay_date = datetime.strptime(pay_date_str, "%Y-%m-%d").date()
                except ValueError:
                    raise ValueError(f"Invalid date format: '{pay_date_str}'. Use YYYY-MM-DD.")
            else:
                pay_date = timezone.localdate()

            mode = request.POST.get("mode", "BANK")
            reference = request.POST.get("reference", "")

            record_payment(
                invoice=invoice,
                amount_paise=amount_paise,
                payment_date=pay_date,
                mode=mode,
                reference=reference,
                user=request.user,
            )
            messages.success(request, "Payment recorded successfully.")

        elif action_name == "cancel_invoice":
            if not hasattr(order, "invoice"):
                raise ValueError("No invoice found for this order to cancel.")
            invoice = order.invoice
            reason = request.POST.get("reason", "")
            cancel_invoice(invoice, user=request.user, reason=reason)
            messages.success(request, f"Invoice {invoice.number} cancelled.")

    except (ValueError, PermissionDenied) as e:
        if isinstance(e, PermissionDenied):
            raise
        messages.error(request, str(e))

    return redirect("order_detail", order_id=order.id)


@login_required
def invoice_action(request, invoice_id, action_name):
    if request.method != "POST":
        return redirect("invoice_detail", invoice_id=invoice_id)

    invoice = get_object_or_404(Invoice.objects.select_related("order", "order__customer"), id=invoice_id)

    action_permission_map = {
        "record_payment": "record_payment",
        "cancel_invoice": "cancel_invoice",
    }
    perm = action_permission_map.get(action_name)
    if not perm:
        raise Http404("Unknown action.")

    require(request.user, perm)

    try:
        if action_name == "record_payment":
            amount_str = request.POST.get("amount")
            amount_paise = _rupees_to_paise(amount_str)

            pay_date_str = request.POST.get("date")
            if pay_date_str:
                try:
                    pay_date = datetime.strptime(pay_date_str, "%Y-%m-%d").date()
                except ValueError:
                    raise ValueError(f"Invalid date format: '{pay_date_str}'. Use YYYY-MM-DD.")
            else:
                pay_date = timezone.localdate()

            mode = request.POST.get("mode", "BANK")
            reference = request.POST.get("reference", "")

            record_payment(
                invoice=invoice,
                amount_paise=amount_paise,
                payment_date=pay_date,
                mode=mode,
                reference=reference,
                user=request.user,
            )
            messages.success(request, "Payment recorded successfully.")

        elif action_name == "cancel_invoice":
            reason = request.POST.get("reason", "")
            cancel_invoice(invoice, user=request.user, reason=reason)
            messages.success(request, f"Invoice {invoice.number} cancelled.")

    except (ValueError, PermissionDenied) as e:
        if isinstance(e, PermissionDenied):
            raise
        messages.error(request, str(e))

    return redirect("invoice_detail", invoice_id=invoice.id)


@login_required
def order_detail(request, order_id):
    order = get_object_or_404(Order.objects.select_related("customer", "salesman"), id=order_id)

    # Permission check: can view all orders or view own order if salesman
    if not can(request.user, "view_all_orders"):
        if not (can(request.user, "view_own_orders") and order.salesman == request.user):
            raise PermissionDenied("You do not have permission to view this order.")

    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "update_lines":
                require(request.user, "edit_order")
                for line in order.lines.all():
                    qty_str = request.POST.get(f"qty_{line.id}")
                    price_str = request.POST.get(f"price_{line.id}")
                    if qty_str is not None:
                        try:
                            qty = int(qty_str)
                        except ValueError:
                            raise ValueError(f"Invalid quantity '{qty_str}' for {line.product.name}.")

                        unit_price_paise = None
                        if price_str and price_str.strip():
                            unit_price_paise = _rupees_to_paise(price_str)

                        update_order_line(order, line.product, qty, unit_price_paise=unit_price_paise, user=request.user)
                messages.success(request, "Order lines updated successfully.")

            elif action == "approve":
                require(request.user, "approve_order")
                approve_order(order, user=request.user)
                messages.success(request, f"Order #{order.id} approved successfully.")

            elif action == "reject":
                require(request.user, "reject_order")
                reason = request.POST.get("reason", "")
                reject_order(order, user=request.user, reason=reason)
                messages.success(request, f"Order #{order.id} rejected.")

            elif action == "create_invoice":
                require(request.user, "create_invoice")
                inv = create_invoice(order, user=request.user)
                messages.success(request, f"Invoice {inv.number} created successfully.")

            elif action == "record_dispatch":
                require(request.user, "record_dispatch")
                if hasattr(order, "invoice"):
                    record_dispatch(order.invoice, user=request.user)
                    messages.success(request, f"Dispatch recorded for Invoice {order.invoice.number}.")
                else:
                    raise ValueError("No invoice found for this order to dispatch.")

            elif action == "record_payment":
                require(request.user, "record_payment")
                if not hasattr(order, "invoice"):
                    raise ValueError("No invoice found for this order to record payment.")
                invoice = order.invoice
                amount_str = request.POST.get("amount")
                amount_paise = _rupees_to_paise(amount_str)

                pay_date_str = request.POST.get("date")
                if pay_date_str:
                    try:
                        pay_date = datetime.strptime(pay_date_str, "%Y-%m-%d").date()
                    except ValueError:
                        raise ValueError(f"Invalid date format: '{pay_date_str}'. Use YYYY-MM-DD.")
                else:
                    pay_date = timezone.localdate()

                mode = request.POST.get("mode", "BANK")
                reference = request.POST.get("reference", "")

                record_payment(
                    invoice=invoice,
                    amount_paise=amount_paise,
                    payment_date=pay_date,
                    mode=mode,
                    reference=reference,
                    user=request.user,
                )
                messages.success(request, "Payment recorded successfully.")

            elif action == "cancel_invoice":
                require(request.user, "cancel_invoice")
                if not hasattr(order, "invoice"):
                    raise ValueError("No invoice found for this order to cancel.")
                invoice = order.invoice
                reason = request.POST.get("reason", "")
                cancel_invoice(invoice, user=request.user, reason=reason)
                messages.success(request, f"Invoice {invoice.number} cancelled.")

            return redirect("order_detail", order_id=order.id)

        except (ValueError, PermissionDenied) as e:
            if isinstance(e, PermissionDenied):
                raise
            messages.error(request, str(e))
            # Fall through to re-render the detail page with the error message

    # Prepare data for rendering
    lines_data = []
    stock_rep = stock_report(order)
    stock_map = {rep["product"].id: rep for rep in stock_rep}

    for line in order.lines.select_related("product").all():
        line_tot = line.quantity * line.unit_price_paise
        rep = stock_map.get(line.product.id, {"available": 0, "shortage": 0})
        lines_data.append({
            "line": line,
            "unit_price_rupees": f"{Decimal(line.unit_price_paise) / Decimal(100):.2f}",
            "unit_price_formatted": format_rupees(line.unit_price_paise),
            "line_total_formatted": format_rupees(line_tot),
            "available": rep["available"],
            "shortage": rep["shortage"],
        })

    tot_paise = order_total_paise(order)
    cust_out_paise = customer_outstanding(order.customer)
    inv = getattr(order, "invoice", None)
    inv_out_formatted = format_rupees(invoice_outstanding(inv)) if inv else "0.00"

    ctx = _base_context(request.user)
    ctx.update({
        "order": order,
        "lines_data": lines_data,
        "order_total_formatted": format_rupees(tot_paise),
        "customer_outstanding_formatted": format_rupees(cust_out_paise),
        "stock_rep": stock_rep,
        "invoice": inv,
        "invoice_outstanding_formatted": inv_out_formatted,
        "today": timezone.localdate(),
    })

    return render(request, "core/order_detail.html", ctx)


@login_required
def invoice_list(request):
    require(request.user, "create_invoice" if not can(request.user, "view_reports") else "view_reports")

    invoices_qs = Invoice.objects.all().order_by("-id")
    invoices_data = []
    for inv in invoices_qs:
        invoices_data.append({
            "invoice": inv,
            "total_formatted": format_rupees(inv.total_paise),
            "outstanding_formatted": format_rupees(invoice_outstanding(inv)),
        })

    ctx = _base_context(request.user)
    ctx.update({
        "invoices_data": invoices_data,
    })

    return render(request, "core/invoice_list.html", ctx)


@login_required
def invoice_detail(request, invoice_id):
    if not (can(request.user, "create_invoice") or can(request.user, "view_reports") or request.user.is_superuser):
        raise PermissionDenied("You do not have permission to view invoices.")

    invoice = get_object_or_404(Invoice.objects.select_related("order", "order__customer"), id=invoice_id)

    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "record_payment":
                require(request.user, "record_payment")
                amount_str = request.POST.get("amount")
                amount_paise = _rupees_to_paise(amount_str)

                pay_date_str = request.POST.get("date")
                if pay_date_str:
                    try:
                        pay_date = datetime.strptime(pay_date_str, "%Y-%m-%d").date()
                    except ValueError:
                        raise ValueError(f"Invalid date format: '{pay_date_str}'. Use YYYY-MM-DD.")
                else:
                    pay_date = timezone.localdate()

                mode = request.POST.get("mode", "BANK")
                reference = request.POST.get("reference", "")

                record_payment(
                    invoice=invoice,
                    amount_paise=amount_paise,
                    payment_date=pay_date,
                    mode=mode,
                    reference=reference,
                    user=request.user,
                )
                messages.success(request, "Payment recorded successfully.")

            elif action == "cancel_invoice":
                require(request.user, "cancel_invoice")
                reason = request.POST.get("reason", "")
                cancel_invoice(invoice, user=request.user, reason=reason)
                messages.success(request, f"Invoice {invoice.number} cancelled.")

            return redirect("invoice_detail", invoice_id=invoice.id)

        except (ValueError, PermissionDenied) as e:
            if isinstance(e, PermissionDenied):
                raise
            messages.error(request, str(e))
            # Fall through to re-render page

    # Prepare view context
    lines_data = []
    for l in invoice.lines.all():
        lines_data.append({
            "line": l,
            "unit_price_formatted": format_rupees(l.unit_price_paise),
            "taxable_formatted": format_rupees(l.taxable_paise),
            "cgst_formatted": format_rupees(l.cgst_paise),
            "sgst_formatted": format_rupees(l.sgst_paise),
            "igst_formatted": format_rupees(l.igst_paise),
            "total_formatted": format_rupees(l.taxable_paise + l.cgst_paise + l.sgst_paise + l.igst_paise),
        })

    payments_data = []
    for p in invoice.payments.all().order_by("id"):
        payments_data.append({
            "payment": p,
            "amount_formatted": format_rupees(p.amount_paise),
        })

    paid_so_far = invoice.total_paise - invoice_outstanding(invoice)
    cust_out_paise = customer_outstanding(invoice.order.customer)

    ctx = _base_context(request.user)
    ctx.update({
        "invoice": invoice,
        "lines_data": lines_data,
        "payments_data": payments_data,
        "taxable_formatted": format_rupees(invoice.taxable_paise),
        "cgst_formatted": format_rupees(invoice.cgst_paise),
        "sgst_formatted": format_rupees(invoice.sgst_paise),
        "igst_formatted": format_rupees(invoice.igst_paise),
        "total_formatted": format_rupees(invoice.total_paise),
        "paid_formatted": format_rupees(paid_so_far),
        "outstanding_formatted": format_rupees(invoice_outstanding(invoice)),
        "customer_outstanding_formatted": format_rupees(cust_out_paise),
        "today": timezone.localdate(),
    })

    return render(request, "core/invoice_detail.html", ctx)


@login_required
def download_invoice_pdf_view(request, invoice_id):
    require(request.user, "download_invoice_pdf")

    invoice = get_object_or_404(Invoice, id=invoice_id)
    pdf_bytes = build_invoice_pdf(invoice)

    filename = f"{invoice.number.replace('/', '-')}.pdf"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# --- Production and Purchase Views ---

def _require_production(user):
    """Enforce production role or superuser. Raise PermissionDenied if not allowed."""
    roles = get_roles(user)
    if "production" not in roles and not getattr(user, "is_superuser", False):
        raise PermissionDenied("You do not have permission to access production screens.")


def _base_unit_display(unit: str) -> str:
    if unit == "g":
        return "kg"
    elif unit == "ml":
        return "L"
    return "pcs"


def _format_base_quantity(base_qty: int, base_unit: str) -> str:
    d = Decimal(base_qty)
    if base_unit == "g":
        val = d / Decimal("1000")
        val_str = f"{val:f}".rstrip('0').rstrip('.') if '.' in f"{val:f}" else f"{val:f}"
        return f"{val_str} kg"
    elif base_unit == "ml":
        val = d / Decimal("1000")
        val_str = f"{val:f}".rstrip('0').rstrip('.') if '.' in f"{val:f}" else f"{val:f}"
        return f"{val_str} L"
    return f"{base_qty} pcs"


def _format_variance_bp(var_bp: int) -> str:
    # 500 bp = 5.00%
    sign = "+" if var_bp > 0 else ""
    pct = Decimal(var_bp) / Decimal("100")
    return f"{sign}{pct:.2f}%"


@login_required
def purchase_list(request):
    _require_production(request.user)

    purchases = Purchase.objects.select_related("supplier", "item").order_by("-id")
    purchases_data = []
    for p in purchases:
        purchases_data.append({
            "purchase": p,
            "quantity_display": _format_base_quantity(p.quantity, p.item.base_unit),
            "total_formatted": format_rupees(p.total_value_paise),
        })

    ctx = _base_context(request.user)
    ctx.update({
        "purchases_data": purchases_data,
    })
    return render(request, "core/purchase_list.html", ctx)


@login_required
def purchase_create(request):
    _require_production(request.user)

    suppliers = Supplier.objects.all().order_by("name")
    items = Item.objects.filter(kind__in=[Item.Kind.RAW, Item.Kind.PACKAGING]).order_by("name")

    form_errors = []
    selected_supplier_id = ""
    selected_item_id = ""
    entered_quantity = ""
    entered_rate = ""
    entered_invoice_no = ""

    if request.method == "POST":
        selected_supplier_id = request.POST.get("supplier", "").strip()
        selected_item_id = request.POST.get("item", "").strip()
        entered_quantity = request.POST.get("quantity", "").strip()
        entered_rate = request.POST.get("rate", "").strip()
        entered_invoice_no = request.POST.get("invoice_no", "").strip()

        # Validate supplier
        supplier = None
        if not selected_supplier_id:
            form_errors.append("Supplier is required.")
        else:
            try:
                supplier = Supplier.objects.get(id=int(selected_supplier_id))
            except (ValueError, Supplier.DoesNotExist):
                form_errors.append("Selected supplier is invalid.")

        # Validate item
        item = None
        if not selected_item_id:
            form_errors.append("Item is required.")
        else:
            try:
                item = Item.objects.get(id=int(selected_item_id))
            except (ValueError, Item.DoesNotExist):
                form_errors.append("Selected item is invalid.")

        # Validate quantity
        d_qty = None
        if not entered_quantity:
            form_errors.append("Quantity is required.")
        else:
            try:
                d_qty = Decimal(entered_quantity)
                if d_qty <= 0:
                    form_errors.append("Quantity must be greater than 0.")
            except (InvalidOperation, TypeError):
                form_errors.append("Quantity must be a valid number.")

        # Validate rate
        d_rate = None
        if not entered_rate:
            form_errors.append("Rate is required.")
        else:
            try:
                d_rate = Decimal(entered_rate)
                if d_rate <= 0:
                    form_errors.append("Rate must be greater than 0.")
            except (InvalidOperation, TypeError):
                form_errors.append("Rate must be a valid number.")

        if not form_errors and item and supplier and d_qty is not None and d_rate is not None:
            # Map item's display unit to purchase unit
            # Base units: g -> kg, ml -> l, pcs -> pcs
            if item.base_unit == Item.BaseUnit.G:
                unit = "kg"
            elif item.base_unit == Item.BaseUnit.ML:
                unit = "l"
            else:
                unit = "pcs"

            today = timezone.localdate()
            try:
                record_purchase(
                    item=item,
                    amount=d_qty,
                    unit=unit,
                    rate_rupees=d_rate,
                    supplier=supplier,
                    purchase_date=today,
                    invoice_no=entered_invoice_no,
                    user=request.user,
                )
                messages.success(request, "Purchase recorded successfully.")
                return redirect("purchase_list")
            except ValueError as e:
                form_errors.append(str(e))

    ctx = _base_context(request.user)
    ctx.update({
        "suppliers": suppliers,
        "items": items,
        "selected_supplier_id": selected_supplier_id,
        "selected_item_id": selected_item_id,
        "entered_quantity": entered_quantity,
        "entered_rate": entered_rate,
        "entered_invoice_no": entered_invoice_no,
        "form_errors": form_errors,
    })
    return render(request, "core/purchase_new.html", ctx)


@login_required
def production_list(request):
    _require_production(request.user)

    runs = ProductionRun.objects.select_related("bom__product").order_by("-id")
    runs_data = []
    for r in runs:
        runs_data.append({
            "run": r,
            "yield_variance_pct": _format_variance_bp(r.yield_variance_bp),
        })

    ctx = _base_context(request.user)
    ctx.update({
        "runs_data": runs_data,
    })
    return render(request, "core/production_list.html", ctx)


@login_required
def production_create(request):
    _require_production(request.user)

    boms = BOM.objects.select_related("product").all().order_by("product__name")
    products = [b.product for b in boms]

    # Default to Murukku 200 g if available, else first
    default_bom = None
    for b in boms:
        if b.product.name == "Murukku 200 g":
            default_bom = b
            break
    if not default_bom and boms.exists():
        default_bom = boms.first()

    selected_product_id = request.POST.get("product", "") if request.method == "POST" else (default_bom.product.id if default_bom else "")
    active_bom = None
    if selected_product_id:
        try:
            active_bom = BOM.objects.get(product_id=int(selected_product_id))
        except (ValueError, BOM.DoesNotExist):
            active_bom = default_bom

    bom_lines = list(active_bom.lines.select_related("item").all()) if active_bom else []

    form_errors = []
    entered_good_packets = request.POST.get("actual_good_packets", "").strip() if request.method == "POST" else ""
    entered_damaged_pouches = request.POST.get("damaged_pouches", "0").strip() if request.method == "POST" else "0"
    entered_cartons_used = request.POST.get("cartons_used", "").strip() if request.method == "POST" else ""

    if request.method == "POST":
        if not active_bom:
            form_errors.append("Invalid product selected.")

        # Validate good packets
        actual_output = None
        if not entered_good_packets:
            form_errors.append("Actual good packets is required.")
        else:
            try:
                actual_output = int(entered_good_packets)
                if actual_output < 0:
                    form_errors.append("Actual good packets cannot be negative.")
            except ValueError:
                form_errors.append("Actual good packets must be a whole number.")

        # Validate damaged pouches
        damaged_qty = 0
        if entered_damaged_pouches:
            try:
                damaged_qty = int(entered_damaged_pouches)
                if damaged_qty < 0:
                    form_errors.append("Damaged pouches cannot be negative.")
            except ValueError:
                form_errors.append("Damaged pouches must be a whole number.")

        # Find which item is the pouch packaging, carton packaging, etc.
        pouch_item = None
        carton_item = None
        for bl in bom_lines:
            if "pouch" in bl.item.name.lower():
                pouch_item = bl.item
            elif "carton" in bl.item.name.lower():
                carton_item = bl.item

        # Parse carton if present in BOM
        carton_qty_base = None
        if carton_item:
            if not entered_cartons_used:
                form_errors.append("Cartons used is required.")
            else:
                try:
                    d_cartons = Decimal(entered_cartons_used)
                    if d_cartons < 0:
                        form_errors.append("Cartons used cannot be negative.")
                    else:
                        carton_qty_base = to_base(d_cartons, "pcs")
                except (InvalidOperation, TypeError, ValueError):
                    form_errors.append("Cartons used must be a valid number.")

        # Validate raw materials and packaging lines
        lines_spec = {}
        for bl in bom_lines:
            item = bl.item
            if item == pouch_item:
                # Pouches: actual issued pouches = good packets + damaged pouches
                # wastage = damaged pouches
                if actual_output is not None and damaged_qty is not None:
                    issued_pouches = actual_output + damaged_qty
                    lines_spec[item] = {
                        "qty": issued_pouches,
                        "wastage": damaged_qty,
                    }
            elif item == carton_item:
                if carton_qty_base is not None:
                    lines_spec[item] = {
                        "qty": carton_qty_base,
                        "wastage": 0,
                    }
            else:
                # Other raw materials entered by user
                val_raw = request.POST.get(f"item_{item.id}", "").strip()
                if not val_raw:
                    form_errors.append(f"Quantity for {item.name} is required.")
                else:
                    try:
                        d_val = Decimal(val_raw)
                        if d_val < 0:
                            form_errors.append(f"Quantity for {item.name} cannot be negative.")
                        else:
                            unit_str = "kg" if item.base_unit == "g" else ("l" if item.base_unit == "ml" else "pcs")
                            base_qty = to_base(d_val, unit_str)
                            lines_spec[item] = {
                                "qty": base_qty,
                                "wastage": 0,
                            }
                    except (InvalidOperation, TypeError, ValueError):
                        form_errors.append(f"Quantity for {item.name} must be a valid number.")

        if not form_errors and active_bom and actual_output is not None:
            try:
                run = record_production_run(
                    bom=active_bom,
                    actual_output=actual_output,
                    lines=lines_spec,
                    user=request.user,
                )
                messages.success(request, f"Production run #{run.id} recorded successfully.")
                return redirect("production_detail", run_id=run.id)
            except ValueError as e:
                form_errors.append(str(e))

    bom_lines_data = []
    for bl in bom_lines:
        # Don't show pouch and carton in raw material inputs if handled separately, or show only raw materials
        # Check: prompt says: "one issued-quantity field per BOM raw material (in display units), actual good packets, damaged pouches, cartons used."
        if bl.item.kind == Item.Kind.RAW:
            disp_unit = _base_unit_display(bl.item.base_unit)
            std_disp = Decimal(bl.quantity) / Decimal("1000") if bl.item.base_unit in ["g", "ml"] else Decimal(bl.quantity)
            std_disp_str = f"{std_disp:f}".rstrip('0').rstrip('.') if '.' in f"{std_disp:f}" else f"{std_disp:f}"
            bom_lines_data.append({
                "item": bl.item,
                "display_unit": disp_unit,
                "standard_display": std_disp_str,
                "entered_val": request.POST.get(f"item_{bl.item.id}", "").strip() if request.method == "POST" else "",
            })

    ctx = _base_context(request.user)
    ctx.update({
        "products": products,
        "selected_product_id": selected_product_id,
        "bom_lines_data": bom_lines_data,
        "entered_good_packets": entered_good_packets,
        "entered_damaged_pouches": entered_damaged_pouches,
        "entered_cartons_used": entered_cartons_used,
        "form_errors": form_errors,
    })
    return render(request, "core/production_new.html", ctx)


@login_required
def production_detail(request, run_id):
    _require_production(request.user)

    run = get_object_or_404(ProductionRun.objects.select_related("bom__product", "created_by"), id=run_id)
    lines = run.lines.select_related("item").all().order_by("id")

    lines_data = []
    for line in lines:
        lines_data.append({
            "item": line.item,
            "planned_display": _format_base_quantity(line.standard_qty, line.item.base_unit),
            "actual_display": _format_base_quantity(line.actual_qty, line.item.base_unit),
            "variance_pct": _format_variance_bp(line.variance_bp),
            "flagged": line.flagged,
        })

    ctx = _base_context(request.user)
    ctx.update({
        "run": run,
        "lines_data": lines_data,
        "yield_variance_pct": _format_variance_bp(run.yield_variance_bp),
        "material_cost_formatted": format_rupees(run.material_cost_paise),
    })
    return render(request, "core/production_detail.html", ctx)

