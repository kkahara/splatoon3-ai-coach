/**
 * Enter in the voucher field equals Accept and never submits the parent form.
 * IME composition and in-flight / already-accepted states are ignored for apply.
 */
export function onVoucherEnter(event, voucher) {
  if (event.key !== "Enter") {
    return;
  }
  event.preventDefault();
  event.stopPropagation();
  if (event.nativeEvent?.isComposing || event.keyCode === 229) {
    return;
  }
  if (voucher.accepted || voucher.checking) {
    return;
  }
  voucher.apply();
}
