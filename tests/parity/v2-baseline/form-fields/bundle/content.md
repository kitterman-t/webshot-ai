---
title: "Account Preferences Review"
source: "file://<REPO>/tests/fixtures/form_page.html"
captured_at: "<TIMESTAMP>"
schema_version: 1.0
---

# Account Preferences Review

This page verifies that every kind of form field a capture can encounter is preserved as a record: text, email, password, checkbox, radio, select, and textarea. The password value must never reach any published artifact.

## Profile

**display_name:** Avery Fixture

**contact_email:** avery@example.com

**account_password:** [REDACTED PASSWORD]

**release_notes_opt_in:** true

**digest_frequency:** true

**digest_frequency:** false

**timezone:** UTC+01:00 Berlin

**signature_note:** Sent from the quarterly review workstation.

## Why these settings matter

A capture is an act of record. The state of every control on the page is part of what the page said, so each field above must appear in the bundle with its name and value — except the password, whose value is replaced by a redaction marker before it can reach any artifact.
