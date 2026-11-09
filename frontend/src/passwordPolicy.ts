export const passwordMinLength = 12;
export const passwordMaxLength = 200;
export const passwordPolicyError = 'رمز جدید باید بین ۱۲ تا ۲۰۰ نویسه باشد.';
export function validNewPassword(password: string) {
 const length = Array.from(password).length;
 return length >= passwordMinLength && length <= passwordMaxLength;
}
