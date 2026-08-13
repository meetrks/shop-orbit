"""Address book service functions — keeps "exactly one default address per user" honest."""

from django.db import transaction

from .models import Address


def create_address(user, address, *, is_default=False):
    """
    Saves `address` (an unsaved `Address` instance, typically
    `AddressForm.save(commit=False)`) for `user`. Forces `is_default=True`
    for a user's first address (there's no sane UI state for "no
    default"), and otherwise honors the caller's choice, demoting any
    previous default.
    """
    address.user = user
    with transaction.atomic():
        has_existing = Address.objects.filter(user=user).exists()
        address.is_default = is_default or not has_existing
        if address.is_default:
            Address.objects.filter(user=user, is_default=True).update(is_default=False)
        address.save()
    return address


def update_address(address, *, is_default=False):
    """
    Saves an already-persisted `address`, demoting any previous default if
    this one becomes it. Demotion is unconditional on `is_default` rather
    than gated on "was it already the default" — `address` typically
    arrives via `AddressForm.save(commit=False)`, which has already
    applied the new `is_default` value to the instance, so the prior
    on-disk value isn't available to compare against here.
    """
    with transaction.atomic():
        if is_default:
            Address.objects.filter(user_id=address.user_id, is_default=True).exclude(pk=address.pk).update(
                is_default=False
            )
        address.is_default = is_default
        address.save()
    return address


def set_default_address(address):
    """Makes `address` the user's default, demoting whichever one held that spot before."""
    with transaction.atomic():
        Address.objects.filter(user_id=address.user_id, is_default=True).exclude(pk=address.pk).update(
            is_default=False
        )
        if not address.is_default:
            address.is_default = True
            address.save(update_fields=["is_default"])
    return address


def delete_address(address):
    """
    Deletes `address`. If it was the user's default and other addresses
    remain, promotes the most recently created one to default so the user
    is never left without one while they still have addresses saved.
    """
    with transaction.atomic():
        was_default = address.is_default
        user_id = address.user_id
        address.delete()
        if was_default:
            next_address = Address.objects.filter(user_id=user_id).order_by("-created_at").first()
            if next_address:
                next_address.is_default = True
                next_address.save(update_fields=["is_default"])
