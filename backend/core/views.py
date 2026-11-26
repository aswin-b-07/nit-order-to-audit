from decimal import Decimal, InvalidOperation
from datetime import datetime, date

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone

from core.models import Order, Invoice, Payment, Customer
from core.pdf import format_rupees, build_invoice_pdf
from core.permissions import get_roles
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
)


def _require_admin(user):
    """Enforce login and admin or owner role. Raise PermissionDenied (HTTP 403) if not allowed."""
    roles = get_roles(user)
    if "admin" not in roles and "owner" not in roles and not user.is_superuser:
        raise PermissionDenied("You do not have permission to access admin screens.")


def _rupees_to_paise(rupees_str: str) -> int:
    """
    Convert string rupees into integer paise using Decimal.
    Rejects more than 2 decimal places or non-numeric input by raising ValueError.
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

    # Check decimal places
    exponent = d.as_tuple().exponent
    if exponent < -2:
        raise ValueError("Amount cannot have more than 2 decimal places.")

    paise = int(d * Decimal(100))
    return paise


@login_required
def home(request):
    roles = get_roles(request.user)
    is_admin = ("admin" in roles or "owner" in roles or request.user.is_superuser)
    role_display = ", ".join(roles) if roles else ("Superuser" if request.user.is_superuser else "None")

    return render(
        request,
        "core/home.html",
        {
            "user_role": role_display,
            "is_admin": is_admin,
        },
    )


@login_required
def order_list(request):
    _require_admin(request.user)

    status_filter = request.GET.get("status", "").strip()
    orders_qs = Order.objects.select_related("customer", "salesman").all().order_by("-id")
    if status_filter:
        orders_qs = orders_qs.filter(status=status_filter)

    orders_data = []
    for order in orders_qs:
        total_p = order_total_paise(order)
        orders_data.append({
            "order": order,
            "total_formatted": format_rupees(total_p),
        })

    roles = get_roles(request.user)
    role_display = ", ".join(roles) if roles else ("Superuser" if request.user.is_superuser else "None")

    return render(
        request,
        "core/order_list.html",
        {
            "orders_data": orders_data,
            "statuses": [s.value for s in Order.Status],
            "current_status": status_filter,
            "user_role": role_display,
        },
    )


@login_required
def order_detail(request, order_id):
    _require_admin(request.user)

    order = get_object_or_404(Order.objects.select_related("customer", "salesman"), id=order_id)

    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "update_lines":
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
                approve_order(order, user=request.user)
                messages.success(request, f"Order #{order.id} approved successfully.")

            elif action == "reject":
                reason = request.POST.get("reason", "")
                reject_order(order, user=request.user, reason=reason)
                messages.success(request, f"Order #{order.id} rejected.")

            elif action == "create_invoice":
                inv = create_invoice(order, user=request.user)
                messages.success(request, f"Invoice {inv.number} created successfully.")

            elif action == "record_dispatch":
                if hasattr(order, "invoice"):
                    record_dispatch(order.invoice, user=request.user)
                    messages.success(request, f"Dispatch recorded for Invoice {order.invoice.number}.")
                else:
                    raise ValueError("No invoice found for this order to dispatch.")

            return redirect("order_detail", order_id=order.id)

        except ValueError as e:
            messages.error(request, str(e))
            # Fall through to re-render the detail page with the error message

    # Prepare data for rendering
    lines_data = []
    for line in order.lines.select_related("product").all():
        line_tot = line.quantity * line.unit_price_paise
        lines_data.append({
            "line": line,
            "unit_price_rupees": f"{Decimal(line.unit_price_paise) / Decimal(100):.2f}",
            "unit_price_formatted": format_rupees(line.unit_price_paise),
            "line_total_formatted": format_rupees(line_tot),
        })

    tot_paise = order_total_paise(order)
    cust_out_paise = customer_outstanding(order.customer)
    stock_rep = stock_report(order)
    inv = getattr(order, "invoice", None)

    roles = get_roles(request.user)
    role_display = ", ".join(roles) if roles else ("Superuser" if request.user.is_superuser else "None")

    return render(
        request,
        "core/order_detail.html",
        {
            "order": order,
            "lines_data": lines_data,
            "order_total_formatted": format_rupees(tot_paise),
            "customer_outstanding_formatted": format_rupees(cust_out_paise),
            "stock_rep": stock_rep,
            "invoice": inv,
            "user_role": role_display,
        },
    )


@login_required
def invoice_list(request):
    _require_admin(request.user)

    invoices_qs = Invoice.objects.all().order_by("-id")
    invoices_data = []
    for inv in invoices_qs:
        invoices_data.append({
            "invoice": inv,
            "total_formatted": format_rupees(inv.total_paise),
            "outstanding_formatted": format_rupees(invoice_outstanding(inv)),
        })

    roles = get_roles(request.user)
    role_display = ", ".join(roles) if roles else ("Superuser" if request.user.is_superuser else "None")

    return render(
        request,
        "core/invoice_list.html",
        {
            "invoices_data": invoices_data,
            "user_role": role_display,
        },
    )


@login_required
def invoice_detail(request, invoice_id):
    _require_admin(request.user)

    invoice = get_object_or_404(Invoice.objects.select_related("order", "order__customer"), id=invoice_id)

    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "record_payment":
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
                reason = request.POST.get("reason", "")
                cancel_invoice(invoice, user=request.user, reason=reason)
                messages.success(request, f"Invoice {invoice.number} cancelled.")

            return redirect("invoice_detail", invoice_id=invoice.id)

        except ValueError as e:
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

    roles = get_roles(request.user)
    role_display = ", ".join(roles) if roles else ("Superuser" if request.user.is_superuser else "None")

    return render(
        request,
        "core/invoice_detail.html",
        {
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
            "user_role": role_display,
        },
    )


@login_required
def download_invoice_pdf_view(request, invoice_id):
    _require_admin(request.user)

    invoice = get_object_or_404(Invoice, id=invoice_id)
    pdf_bytes = build_invoice_pdf(invoice)

    filename = f"{invoice.number.replace('/', '-')}.pdf"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
