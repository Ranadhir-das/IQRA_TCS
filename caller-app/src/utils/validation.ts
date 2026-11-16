export interface LoginFormErrors {
  username?: string;
  password?: string;
}

export function validateLoginForm(username: string, password: string): LoginFormErrors {
  const errors: LoginFormErrors = {};
  if (!username.trim()) {
    errors.username = 'Please enter your employee username.';
  }
  if (!password) {
    errors.password = 'Please enter your password.';
  }
  return errors;
}

export interface ChangePasswordFormErrors {
  currentPassword?: string;
  newPassword?: string;
  confirmPassword?: string;
}

export function validateChangePasswordForm(
  currentPassword: string,
  newPassword: string,
  confirmPassword: string
): ChangePasswordFormErrors {
  const errors: ChangePasswordFormErrors = {};
  if (!currentPassword) {
    errors.currentPassword = 'Enter your current password.';
  }
  if (!newPassword) {
    errors.newPassword = 'Enter a new password.';
  } else if (newPassword.length < 10) {
    errors.newPassword = 'New password must be at least 10 characters long.';
  } else if (currentPassword && newPassword === currentPassword) {
    errors.newPassword = 'New password must be different from your current password.';
  }
  if (!confirmPassword) {
    errors.confirmPassword = 'Confirm your new password.';
  } else if (newPassword && confirmPassword !== newPassword) {
    errors.confirmPassword = 'New password and confirmation do not match.';
  }
  return errors;
}
