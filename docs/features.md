# Feature reference

[Documentation](README.md) · [Development boundaries](development/README.md)

This guide covers PAK 2.0.0-rc.13. See [Deployment](../deploy/README.md) for installation steps.

## Work reports and grading

| Category | Use |
| --- | --- |
| PM | Preventive maintenance |
| EM | Emergency repairs and breakdown response |
| CM | Corrective maintenance for an identified equipment item |
| Project | Work performed as part of a project |
| Assigned Work (امور محوله) | Activities in a hall with no specific equipment item |

Select an existing hall for Assigned Work and a physical asset for CM. Existing projects retain the category originally recorded. In normal lists and activity details, titles, dates, categories, locations and meaningful equipment identifiers identify the work. The application uses generated report identifiers internally.

Technicians and supervisors can record their own work. A supervisor's own contribution has no self-awarded supervisor grade; the head records a grade from 0 to 9. For a technician in the supervisor's own specialty, the supervisor can record the participant-specific 0-to-3 grade. Other specialties retain their own required approval scope. Zero is a valid grade.

During review, the authorized supervisor can correct participant minutes and add, remove or adjust consumed parts, then grade and approve in one atomic operation. Real edits retain review evidence and PM correction snapshots. Unchanged specialties retain valid prior approvals; a specialty still awaiting its first approval is not silently approved. Historical returns remain readable, while the return-for-correction loop is not used for new supervisor/head decisions.

Each account's inbox shows its own final performance. Personnel-wide grading reports remain restricted to the head.

In personnel performance, use the selected month and year or the all-period view. Select a person's activity chart, then a hall to open the underlying reports. The chart uses the selected person's recorded participation time and counts each completed report once. Hall labels use the equipment's current registered location. An account can view its own activity.

## PM forms and responsibility

Authorized editors can change activity text and add/remove activities through the application's PM editor. A PDF export represents the current saved revision. Issued original submission evidence is preserved separately from later corrections.

Head/manager can select any eligible active supervisor for PM execution, independently of the technical form classification. Correction of untouched issued drafts is audited and clears the previous assignee. The responsible supervisor chooses a technician from the execution team. The original issued identity and completed evidence remain unchanged.

### Review and correct utilities responsibility

1. Sign in as the technical head. Open **برگه‌ها و تقویم PM → بازبینی ارجاع PM**.
2. Under **سرپرست صحیح**, choose Mehrabi by his personnel code and Energy unit. Under **مسئول فعلی**, select Abbaspour by code to review his assignments first. Under **واحد فنی روی برگه**, select Utilities (تأسیسات). Select **بررسی ارجاع‌ها**.
3. Open **برنامه‌های آینده**. Use **بررسی اصلاح مسئول برنامه** on each reviewed plan. Enter the reason and confirm **ثبت مسئول نوبت‌های بعدی**. The action preserves the equipment, schedule, activation and existing issued forms.
4. Open **برگه‌های باز**. Use **بررسی انتقال**, enter the reason, review the named destination and confirm **ثبت انتقال مسئول**. A form with recorded work or approvals needs separate review. **سوابق بسته** retains completed and cancelled records for audit.
5. Run the review with the same Utilities filter and an empty **مسئول فعلی**. Check the remaining future and open counts. Review any remaining records and their actual state. Historical entries may still name the original supervisor.

### Create a PM form for another unit

1. Open **برگه‌ها و تقویم PM → برگهٔ جدید**. Select the hall, technical unit, period and activities. An eligible author can enter a new technical unit in the form.
2. Preview the form and save or submit the proposal, depending on role. The chief approves proposals that require approval.
3. Open the form's plan. Select its equipment, schedule and the active supervisor of the team that will execute it. Review the issuance preview before activation.

Original DOCX/XLSX documents are authenticated downloads from the configured private seed folder. They are not the live editable database.

## Warehouse import

XLSX import detects supported columns, preserves exact part codes including leading zeroes, and previews proposed changes. Applying a reviewed import is atomic. Prices are in rial. Importing master data does not manufacture stock movements or silently change inventory counts.

## Activity reports

Choose all activities or any of the five registration categories, a date range, and grouping by hall or line. The chart includes reports that have completed final approval and that the signed-in user can read. Hall and line are taken from the equipment's current location. Assigned Work groups by its selected hall and has no assigned line.

- **Activity count** counts each report once.
- **Work duration** adds the elapsed minutes recorded on those reports.
- **Participant time** adds each person's recorded minutes. Two people working together for 30 minutes contribute 60 person-minutes.
- **Recorded production downtime** adds valid recorded stop durations. Reports with unknown durations are indicated separately. Overlapping stops can contribute to the sum, and OEE uses its own daily sheets.

Select a bar to see reports for that group with their descriptions, participants, minutes and consumed parts. The detail list uses the same period, category and access scope as the chart.

## Optional OEE

OEE is module version 1 on the core schema-12 application. Activation is an explicit database operation, not a configuration toggle or PM scheduling change. The agreed availability loss is technical stoppage; CIP remains part of operating time. Actual line configuration and production figures are entered by the authorized users after activation. See the [OEE guide](../backend/oee/README.md).

## Shared interface behavior

The management view keeps key actions and counts close together. Users can open secondary sections when they need them. The application uses bundled Persian fonts and shared 24-hour time entry. Valid inputs use `HH:mm`. Historical charts load when opened or when their filters or refresh controls change. Live work queues refresh while visible. Requests coalesce while loading and discard stale filter and session responses. Browser navigation protects unsaved OEE forms.
