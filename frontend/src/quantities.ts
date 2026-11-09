const scale = 1_000_000, limit = 1_000_000_000;
export function validQuantity(value: number, allowNegative = false) {
 return Number.isFinite(value) && (allowNegative ? value !== 0 : value > 0) && Math.abs(value) <= limit && Math.round(value * scale) / scale === value;
}
export function addQuantity(left: number, right: number) {
 if (!validQuantity(left) || !validQuantity(right)) return null;
 const units = Math.round(left * scale) + Math.round(right * scale);
 return Number.isSafeInteger(units) && units <= limit * scale ? units / scale : null;
}
