/** Decimal numbers in input fields: shown with a comma, accepted with a comma or a point.
 *  No imports, so the property tests (ui-tests/properties.spec.ts) can run them without the app. */
export const decimalInput = (v: number) => String(v).replace(".", ",");
export const parseDecimal = (v: string) => (v.trim() === "" ? Number.NaN : Number(v.replace(",", ".")));
