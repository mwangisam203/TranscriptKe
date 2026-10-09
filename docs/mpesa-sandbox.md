# Test M-Pesa checkout with Daraja sandbox

The application already implements M-Pesa Express STK Push. `PAYMENT_MODE=test`
sends requests to `https://sandbox.safaricom.co.ke`. No local mock service or new
payment endpoint is needed to connect the real Daraja sandbox.

1. Register or sign in at the [Safaricom Daraja portal](https://developer.safaricom.co.ke/).
   Create a sandbox application with M-Pesa Express. Obtain its consumer key,
   consumer secret, sandbox PayBill shortcode, STK passkey and test phone number
   from the portal. Use credentials issued together for that sandbox application.
2. Select a demo school by name in the workspace. Each order belongs to one school;
   students never need to enter its database ID. TranscriptsKE collects payment
   using its platform credentials. An administrator enables collection for each
   approved participating school. The development demo seed enables demo schools.
3. Make the running backend reachable through a public HTTPS test origin, using
   your preferred tunnel or development hosting. Set that origin, without a path,
   as `PAYMENT_PUBLIC_URL`. Safaricom cannot call your local `localhost` address.
4. Add the following to `backend/.env`, replacing every placeholder. Do not put
   credentials in JavaScript or commit `.env`:

   ```dotenv
   PAYMENT_COLLECTION_POLICY=before_review
   PAYMENTS_ENABLED=true
   PAYMENT_MODE=test
   PAYMENT_ROUTING_MODE=platform
   PAYMENT_PUBLIC_URL=https://YOUR_PUBLIC_TEST_ORIGIN
   MPESA_CONSUMER_KEY=YOUR_SANDBOX_CONSUMER_KEY
   MPESA_CONSUMER_SECRET=YOUR_SANDBOX_CONSUMER_SECRET
   MPESA_SHORTCODE=YOUR_SANDBOX_PAYBILL_SHORTCODE
   MPESA_PASSKEY=YOUR_SANDBOX_STK_PASSKEY
   ```

   Keep the existing application `SECRET_KEY` stable: it signs callback tokens.
   Restart the backend after changing its configuration.
5. In the workspace, save enrollment details at that institution, choose documents
   and recipients, then review the quote and sign consent. New orders use
   `PAYMENT_COLLECTION_POLICY=before_review`: the private checkout is sent to the
   institution only after confirmed payment. Record matching happens after payment
   and is still required before documents can be processed or released.
6. Open checkout, choose M-Pesa, enter the portal's sandbox test
   phone number and press **Start payment**. The server sends the fixed order
   amount to Daraja; the browser cannot choose the charge amount. Sandbox testing
   validates requests and outcomes; do not assume it guarantees a real handset
   prompt. Live credentials and provider approval are needed for live collection.
7. Check the sandbox result and use **Check payment status**. Acceptance of an STK
   request alone does not mark an order paid. The callback must be authenticated
   and match the request, and the server checks the result with Daraja. Pending or
   unknown outcomes must be resolved before another payment attempt.

The application generates its own per-attempt callback address:
`/api/v1/payments/webhooks/mpesa/payments/{payment_id}?token=...`. The origin in
step 3 must forward this path and preserve its query string. Do not log or share
callback tokens. PINs are entered only in the provider's phone prompt, never in
this website.

To run existing isolated payment checks, from `backend/`:

```bash
uv run pytest -q tests/test_payments.py
```

Those tests use fake providers, not your sandbox credentials. Complete Daraja
acceptance separately, including success, cancellation, pending outcomes and
callback/query recovery, before enabling live collection. M-Pesa refunds require
separate reversal credentials; they are not required merely to start checkout.


The development catalog starts at KES 5 per document copy. The local demo schools
have been updated to that price. Quantities multiply the per-copy fee; existing
submitted quotes and payment attempts retain their original amounts. Start a new
checkout to use a changed price. A reduced price does not change sandbox mode or
prove that a handset prompt was delivered.

If no prompt arrives, inspect the saved payment status first. `unknown` without a
provider checkout reference means acceptance could not be confirmed. Do not resend
an unresolved STK request or mark it paid manually. Verify the sandbox app's matching
shortcode/passkey and inspect the Daraja transaction result. Successful OAuth only
confirms the consumer credentials; it does not confirm STK acceptance.


## When no prompt arrives

Check STK initiation separately from callback delivery. OAuth returning a token
is not proof that the M-Pesa Express request was accepted. An explicit
`404.001.03` / Invalid Access Token response without a CheckoutRequestID means
initiation was rejected. Check that the consumer key and secret belong to the
same sandbox app with M-Pesa Express enabled; use the matching sandbox shortcode
and passkey. Restart the backend after changing credentials. If a newly generated
token is still rejected, test the app in Daraja's simulator and investigate its
API access with Safaricom support. Do not copy access tokens into frontend code.

The app marks recognized STK authorization refusals as failed, records a safe
explanation, and permits a new payment attempt after configuration is corrected.
A network timeout or unrecognized provider error stays uncertain. Old attempts
with no provider reference are not silently reclassified or resent.

`ERR_NGROK_3200` means the configured endpoint is offline. Run `ngrok http 8000`
while the backend runs in another terminal. Set `PAYMENT_PUBLIC_URL` to the
active forwarding URL and restart the backend. Verify that `PUBLIC_URL/health`
returns the app's JSON health response before testing callbacks.
