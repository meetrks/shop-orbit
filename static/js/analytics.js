/*
 * GA4 ecommerce event helpers. `gtag`/`dataLayer` are always defined (see
 * base.html), so these pushes are harmless even when GA_MEASUREMENT_ID
 * isn't configured — gtag.js just isn't loaded to report them anywhere.
 *
 * Delegated listener, not one per form, so this keeps working for forms
 * rendered after page load (e.g. via HTMX swaps) without re-binding.
 */
document.addEventListener("submit", function (event) {
    var form = event.target.closest(".js-track-add-to-cart");
    if (!form) return;

    var quantityInput = form.querySelector("[name=quantity]");
    var quantity = quantityInput ? parseInt(quantityInput.value, 10) || 1 : 1;

    var variantSelect = form.querySelector("[name=variant_id]");
    var selectedOption = variantSelect ? variantSelect.options[variantSelect.selectedIndex] : null;
    var price = selectedOption ? selectedOption.dataset.price : form.dataset.price;

    gtag("event", "add_to_cart", {
        currency: "INR",
        value: parseFloat(price) * quantity,
        items: [
            {
                item_id: form.dataset.itemId,
                item_name: form.dataset.itemName,
                price: parseFloat(price),
                quantity: quantity,
            },
        ],
    });
});
