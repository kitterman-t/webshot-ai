# Portfolio Planning - Release Notes

Everything below is present in the DOM. Two of the three blocks are truncated by CSS alone.

## Summary

Portfolio planning gained a scenario comparison view in this release, so a planner can hold two funding shapes side by side without duplicating the portfolio. The capacity model now reconciles against the resource calendar nightly rather than on demand, which removes the stale figures that made the old comparison hard to trust. Allocation rounding moved from the request to the plan, and the audit trail records who changed a scenario as well as when. The fourth line of this paragraph is where the clamp bites: CLAMPEDLINECLAMPOMEGA is present in the document and is not painted, because the browser stops after three lines and clips the rest of the box without removing any of it from the tree.

## Known issues

Scenario comparison does not yet carry custom attributes across a copy, so a scenario cloned from a plan with custom fields loses them silently. The workaround is to re-apply the attribute set after cloning.

Nightly reconciliation runs in the tenant's own timezone, which means a portfolio spanning regions sees one region's figures lag by a day. CLAMPEDCOLLAPSESIGMA marks the part of this list a reader never sees, because the container is four and a half lines tall and the content is considerably taller than that.

## Unaffected

This paragraph is not clamped by anything, and UNCLAMPEDBASELINEALPHA should appear in both the extracted text and the rendered page whether or not the clamp release works. It is here so the case cannot pass by accident on a capture that dropped every block.

## Roomy

ROOMYCLAMPNEVERBITES - this block sets a line clamp of eight lines and holds two, so the clamp is present and clips nothing.
