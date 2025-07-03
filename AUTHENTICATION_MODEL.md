## Authentication Model for HotelChat

### Current State (July 2025)

- **Guests:** No login required. All guest/AI chat features are accessible without authentication.
- **Admin:** Login is required only for admin features such as the dashboard (`/dashboard`) and logout (`/logout`).
- **Login Page:** Remains available for admin access, but not required for general use.

### How to Re-enable Login for All Users

1. Re-add the `@login_required` decorator to any routes you wish to protect.
2. Update the UI to prompt for login as needed.

### Rationale

- This model allows seamless guest interaction with the AI while keeping admin features secure.
- The login system and user table remain in place for future expansion.
