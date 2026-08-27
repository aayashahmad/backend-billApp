# Privacy Policy

**Last updated: 27 August 2026**

> **Before you publish this, replace every `[BRACKETED]` placeholder below.**
> Google Play requires the policy to be reachable at a public URL, and the
> details must match what your Data Safety form declares. This document
> describes what the app actually does today — it is a starting point written
> by the developer, not legal advice. Have a solicitor review it before you
> rely on it, particularly if you operate outside India.

`[APP NAME]` ("the app", "we", "us") is operated by `[YOUR LEGAL NAME OR
BUSINESS NAME]`, `[YOUR BUSINESS ADDRESS]`. This policy explains what the app
collects, why, and what control you have.

---

## 1. Who this policy is for

The app has two kinds of people in it, and the distinction matters:

- **Shop owners** — people who create an account and sign in. You are our user.
- **Customers** — people a shop owner records a bill against. They never sign
  in and have no account. Their details are entered *by the shop owner*.

If you are a shop owner, **you are responsible for the customer information you
enter**. You should only record details you are entitled to hold, and you
should tell your customers you keep a record of their purchases and balances.

---

## 2. What we collect

### Information you give us when you create an account

| Data | Why |
|---|---|
| Name | Identifies your account and appears on bills |
| Email address | Sign-in identifier and account recovery |
| Phone number | Alternative sign-in identifier |
| Password | Authentication — stored only as a bcrypt hash, never in readable form |

### Business details you choose to add

Business name, address, contact email, contact phone numbers, registration
number, and a bill footer note. These are optional. They are printed on the
bills and PDF receipts you generate, so they will be visible to any customer
you give a bill to.

### Information you record about your customers

| Data | Why |
|---|---|
| Customer name | Identifies who a bill belongs to |
| Customer phone number | Matches repeat customers to their existing record |
| Item name, quantity, rate, total | The bill itself |
| Amount paid and outstanding balance | Tracks what a customer still owes |
| Payment method (cash, online, cheque) | Record-keeping |
| Transaction reference or cheque number | Proof of payment |
| Payment screenshots and cheque images | Proof of payment |

### Information collected automatically

Nothing beyond what is needed to serve your requests. **The app contains no
analytics, no advertising SDKs, no tracking libraries, and no third-party
behavioural profiling.** We do not collect your location, contacts, calendar,
microphone, or camera roll beyond the single image you explicitly attach to a
bill.

---

## 3. Device permissions

| Permission | When it is used |
|---|---|
| **Camera** | Only when you tap "Take photo" to capture a payment screenshot or cheque image |
| **Photo library** | Only when you tap "Choose from gallery" to attach an existing image |

Both are requested at the moment you use them, never on launch. Declining them
leaves the rest of the app fully usable — you simply cannot attach an image.
We access only the specific image you pick. We do not scan or upload your
photo library.

---

## 4. How your information is stored and protected

- Data is stored in a PostgreSQL database on `[YOUR HOSTING PROVIDER, e.g.
  Render, in the Oregon, USA region]`.
- Passwords are hashed with bcrypt. We cannot read your password, and neither
  can anyone with database access.
- Sessions use signed JSON Web Tokens, held in the device's secure storage
  (iOS Keychain / Android Keystore), not in plain files.
- Payment screenshots and cheque images are stored in the database and served
  only through an authenticated endpoint that verifies you own the bill.
- **Each shop owner's data is isolated.** One owner cannot read, search, or
  download another owner's customers, bills, or images.

No system is perfectly secure, and we cannot guarantee absolute security.

**Encryption in transit:** `[CONFIRM YOU SERVE THE API OVER HTTPS BEFORE
PUBLISHING — Play Store's Data Safety form asks this directly, and the answer
must be truthful. A deployment served over plain HTTP would need this
paragraph rewritten.]`

---

## 5. Who we share it with

**We do not sell your data. We do not share it with advertisers. We do not
share it with data brokers.**

Data is disclosed only to:

- **Our hosting provider** (`[PROVIDER]`), which stores the database on our
  behalf and cannot use it for its own purposes.
- **Anyone you send a bill to.** When you print or share a PDF, that document
  leaves the app and we no longer control it.
- **Law enforcement**, where we are legally required to comply with a valid
  order.

---

## 6. How long we keep it

Your account and its records are kept until you ask us to delete them. Bills
are business records, so we do not delete them automatically — many
jurisdictions require you to retain them for several years.

---

## 7. Your rights

You may ask us to:

- **Access** a copy of the data held about you
- **Correct** anything inaccurate
- **Delete** your account and its data
- **Export** your records

Contact `[YOUR CONTACT EMAIL]`. We aim to respond within 30 days.

If you are in the EU/UK, you additionally have rights under the GDPR,
including the right to object to processing and to complain to your national
data protection authority. `[IF YOU SERVE EU/UK USERS, CONFIRM YOUR LAWFUL
BASIS AND WHETHER YOU NEED AN EU REPRESENTATIVE.]`

### Deleting your account

`[DESCRIBE THE ACTUAL METHOD. Play Store requires that users can request
account deletion, and since the app has no in-app delete button today, this
must be a working email address or a web form that you monitor.]`

---

## 8. Children

The app is a business tool and is not directed at children under 13 (or under
16 in the EU/UK). We do not knowingly collect data from children. If you
believe a child has provided us data, contact us and we will delete it.

---

## 9. Changes to this policy

We will update the "Last updated" date above when this policy changes. For
material changes we will give notice in the app before they take effect.

---

## 10. Contact

`[YOUR NAME OR BUSINESS]`
`[YOUR CONTACT EMAIL]`
`[YOUR BUSINESS ADDRESS]`
