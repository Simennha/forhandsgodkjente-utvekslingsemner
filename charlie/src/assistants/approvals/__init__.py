"""Approvals: passkey (WebAuthn) on the phone for level 3, approval box for large things.

Flow:
1. The executor stores a pending proposal and calls ``ApprovalService.request`` with the
   proposal id, the params hash, a fresh random challenge and the code-rendered text.
2. The owner approves on the device. The device signs
   ``approval_message(proposal_id, params_hash, challenge, method[, text])``.
3. ``ApprovalService.approve`` verifies that signature against the REGISTERED device key and
   returns an attestation signed with the approvals service key.
4. The executor verifies the attestation (Ed25519 public key) and checks that it matches the
   stored proposal's id, params hash, challenge and method. Each pending request can be
   approved once; the executor's compare-and-set status change stops replays.
"""
