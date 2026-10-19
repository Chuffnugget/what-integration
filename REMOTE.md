# Remote WHAT API Connections

Add a named installation in **WHAT > Settings > Home Assistant Installations**.
Choose **Home Assistant API** and enter the Home Assistant HTTPS address, such as
its normal Nabu Casa remote-access URL. Leave the address blank for direct PC
connections only. Save, then copy that installation's Pairing details.

In Home Assistant, add WHAT and choose **WHAT API (Recommended)**.
Enter the copied PC device ID, pairing token, fingerprint and a PC name. Leave the
optional PC address blank for remote access. The entry
waits for an authenticated connection; it does not need to reach the PC locally.
An existing locally paired entry can also enable outbound API access from WHAT.
Reconfigure the existing entry when changing its pairing details to keep entity IDs.

The PC opens an outbound TLS WebSocket to `/api/what/connect`. Do not forward the
PC's listening port through your router. Custom proxies must forward WebSocket
upgrades and present a certificate trusted by the PC. Extra proxy login pages,
URL subpaths and embedded credentials are unsupported. HTTPS defaults to port 443.

Each installation connects independently. Local access takes preference only for
that same installation. Entries paired directly probe their local address every
30 seconds while using an outbound route. Remote-only entries do not probe a
placeholder local address. Failed connections retry with backoff. Stopping WHAT
stops all connections; disabling an installation stops only that one.

## Permissions and Identity

Each installation has a separate random token. WHAT sends it inside verified TLS,
never in the URL. It does not store a broad Home Assistant account access token.
Remote connections use the sensor browser's Cloud selections. Module permissions
still apply, and an installation can be restricted to readings only.

The PC device ID stays the same across installations. Each Home Assistant owns
its own entity registry, custom names and history. Commands are scoped to their
authenticated session, expire and are not automatically retried. Deleting an
installation revokes its access without deleting Home Assistant entities.

## Troubleshooting

- **HTTP 404:** update the WHAT integration and restart Home Assistant. Check that
  your proxy forwards `/api/what/connect`. Replacing a token does not fix a missing route.
- **Authentication rejected:** check that the token belongs to this named installation
  and reconfigure the matching Home Assistant entry with its pairing details.
- **TLS failure:** check the certificate, hostname and PC clock. Verification remains enabled.
- **Waiting for Home Assistant:** check the installation row for connection status.
  Direct connections need a reachable PC address and Windows Firewall access.

Local fixtures cover TLS and concurrent outbound streams. A real Nabu Casa or
reverse-proxy deployment still needs testing on your own instance.
