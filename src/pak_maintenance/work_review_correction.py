"""Audited supervisor corrections create a fresh review revision, never a final rewrite."""
from copy import deepcopy
from uuid import uuid4

from .common import ApiError, audit, now, dumps, loads
from .quantity import decimal_value, value


def _values(body):
    return {
        "startTime": body.get("startTime"), "endTime": body.get("endTime"),
        "durationMinutes": body.get("durationMinutes"),
        "participants": sorted((dict(personId=p["personId"], minutes=p["minutes"], specialty=p["specialty"])
                                for p in body.get("participants", [])), key=lambda p: p["personId"]),
        "parts": sorted((dict(partId=p["partId"], quantity=decimal_value(p["quantity"]))
                         for p in body.get("parts", [])), key=lambda p: p["partId"]),
    }


class ReviewCorrectionMixin:
    def _carry_unchanged_supervisor_approvals(self, c, user, row, original, normalized, revision, stamp):
        """Reuse documented evaluations for unchanged people of other units.

        The original actor and evaluation time remain historical facts. A
        separate event identifies the actual correcting actor and source IDs;
        these rows never claim the previous actor performed a fresh review.
        """
        old_people = {p["personId"]: p for p in original["participants"]}
        new_people = {p["personId"]: p for p in normalized["participants"]}
        prior = c.query("SELECT a.*,u.name AS actor_name FROM approvals a JOIN users u ON u.id=a.actor_id "
            "WHERE a.order_id=:id AND a.revision=:rev AND a.stage='supervisor' AND a.decision='approve' "
            "AND a.specialty<>:specialty ORDER BY a.created_at DESC,a.id DESC",
            {"id": row["id"], "rev": row["workflow_revision"], "specialty": user.specialty})
        # Freeze the actor name from the actual approval event. A copied
        # approval can itself be carried again, so retain its first identity.
        carry_revision = "CAST(json_extract(details,'$.revision') AS INTEGER)" if c.is_sqlite else "CAST(details::jsonb->>'revision' AS INTEGER)"
        prior_carries = {}
        for event in c.query("SELECT action,actor_name,details FROM audit WHERE entity='order' AND entity_id=:id "
            "AND action IN ('approve','carry-supervisor-approval') AND " + carry_revision + "=:rev",
            {"id": row["id"], "rev": row["workflow_revision"]}):
            provenance = loads(event["details"])
            if not isinstance(provenance, dict) or not isinstance(provenance.get("approvalId"), str) or not provenance["approvalId"]:
                continue  # Older approval audits may not link to an approval row.
            prior_carries[provenance["approvalId"]] = (provenance["sourceActorName"]
                if event["action"] == "carry-supervisor-approval" else event["actor_name"])
        seen, carried = set(), []
        for source in prior:
            specialty = source["specialty"]
            if specialty in seen:
                continue
            seen.add(specialty)
            people = c.query("SELECT p.person_id,COALESCE(gp.policy,'two-stage-v1') AS policy FROM participants p "
                "LEFT JOIN participant_grading_policies gp ON gp.order_id=p.order_id AND gp.person_id=p.person_id AND gp.revision=:rev "
                "WHERE p.order_id=:id AND p.specialty=:spec", {"id": row["id"], "rev": row["workflow_revision"], "spec": specialty})
            expected = {p["person_id"] for p in people if p["policy"] != "head-only-v1"}
            if not expected:
                continue
            if any(old_people.get(person) != new_people.get(person) for person in expected):
                raise ApiError(409, "زمان یا هویت افراد تخصص دیگر تغییر کرده است؛ انتقال تأیید قبلی ممکن نیست.")
            evidence = {p["person_id"]: p for p in c.query("SELECT person_id,coefficient,actor_id,created_at FROM participant_scores "
                "WHERE order_id=:id AND revision=:rev AND specialty=:spec AND approval_id=:approval",
                {"id": row["id"], "rev": row["workflow_revision"], "spec": specialty, "approval": source["id"]})}
            grades = []
            for person in sorted(expected):
                previous = evidence.get(person)
                coefficient = previous["coefficient"] if previous else source["coefficient"]
                if coefficient is None or previous and previous["actor_id"] != source["actor_id"]:
                    raise ApiError(409, "سابقهٔ نمرهٔ تأییدشده کامل نیست؛ انتقال خودکار تأیید ممکن نیست.")
                score = decimal_value(coefficient)
                if not 0 <= score <= 3:
                    raise ApiError(409, "سابقهٔ نمرهٔ تأییدشده معتبر نیست.")
                grades.append(dict(personId=person, specialty=specialty, coefficient=score,
                    createdAt=previous["created_at"] if previous else source["created_at"],
                    source="individual" if previous else "legacy-specialty"))
            approval_id = str(uuid4())
            reason = f"تأیید نسخهٔ {row['workflow_revision']} برای افراد بدون تغییر منتقل شد؛ ارزیابی تازه‌ای انجام نشده است."
            if source["reason"]:
                reason += "\nتوضیح ارزیابی اصلی: " + source["reason"]
            c.execute("INSERT INTO approvals(id,order_id,revision,stage,specialty,decision,coefficient,reason,actor_id,created_at) "
                "VALUES(:approval,:id,:revision,'supervisor',:spec,'approve',NULL,:reason,:actor,:stamp)",
                {"approval": approval_id, "id": row["id"], "revision": revision, "spec": specialty,
                 "reason": reason[:2000], "actor": source["actor_id"], "stamp": source["created_at"]})
            for grade in grades:
                c.execute("INSERT INTO participant_scores(order_id,revision,person_id,specialty,coefficient,actor_id,approval_id,created_at) "
                    "VALUES(:id,:revision,:person,:spec,:coefficient,:actor,:approval,:stamp)",
                    {"id": row["id"], "revision": revision, "person": grade["personId"], "spec": specialty,
                     "coefficient": grade["coefficient"], "actor": source["actor_id"], "approval": approval_id, "stamp": grade["createdAt"]})
            provenance = dict(approvalId=approval_id, sourceApprovalId=source["id"], sourceRevision=row["workflow_revision"],
                sourceActorId=source["actor_id"], sourceActorName=prior_carries.get(source["id"], source["actor_name"]), sourceCreatedAt=source["created_at"],
                revision=revision, stage="supervisor", specialty=specialty, grades=grades)
            audit(c, user, "carry-supervisor-approval", "order", row["id"], provenance, created_at=stamp)
            carried.append(provenance)
        return carried

    def review_correct(self, user, order_id, payload):
        from .work_service import text, integer
        self._role(user)
        if user.role != "supervisor" or not user.specialty:
            raise ApiError(403, "اصلاح گزارش در زمان بررسی با سرپرست مسئول است.")
        if not isinstance(payload, dict) or set(payload) - {"version", "reason", "participants", "parts", "startTime", "endTime", "grades"}:
            raise ApiError(400, "اطلاعات اصلاح گزارش نامعتبر است.")
        reason = text(payload, "reason", 2000)

        def operation(c):
            from .security import assert_live_actor
            assert_live_actor(c, user)
            row = self.get_row(c, order_id, user)
            self.version(row, payload.get("version", -1))
            if row["status"] != "supervisor":
                raise ApiError(409, "اصلاح مستقیم فقط هنگام بررسی سرپرست و پیش از تأیید نهایی مجاز است.")
            if not self.can_decide(c, row, user):
                raise ApiError(403, "این گزارش در نوبت بررسی شما نیست یا تخصص شما قبلاً تأیید شده است.")

            previous_revision = row["workflow_revision"]
            lock = "" if c.is_sqlite else " FOR SHARE OF u"
            people = c.query("SELECT p.person_id,p.minutes,p.specialty,u.role,u.active,u.specialty AS current_specialty,"
                "gp.policy,gp.role_at_submission FROM participants p JOIN users u ON u.id=p.person_id "
                "LEFT JOIN participant_grading_policies gp ON gp.order_id=p.order_id AND gp.person_id=p.person_id AND gp.revision=:rev "
                "WHERE p.order_id=:id ORDER BY p.person_id" + lock, {"id": order_id, "rev": previous_revision})
            # A correction is not a new evaluation policy. Historical reports
            # without policies retain their legacy technician/two-stage rule.
            if not people or any(not p["active"] or p["specialty"] != p["current_specialty"] or
                p["role"] != (p["role_at_submission"] or "technician") or
                (p["policy"] or "two-stage-v1") != ("head-only-v1" if p["role"] == "supervisor" else "two-stage-v1")
                for p in people):
                raise ApiError(409, "نقش، تخصص یا وضعیت انجام‌دهنده پس از ارسال تغییر کرده است؛ ابتدا گزارش را از مسیر معمول بازبینی کنید.")

            original = loads(row["body"])
            original["participants"] = [dict(personId=p["person_id"], minutes=p["minutes"], specialty=p["specialty"]) for p in people]
            original["parts"] = [dict(partId=p["part_id"], quantity=value(p["quantity_units"])) for p in
                c.query("SELECT part_id,quantity_units FROM order_parts WHERE order_id=:id ORDER BY part_id", {"id": order_id})]
            candidate = deepcopy(original)
            if "participants" in payload:
                updates = payload["participants"]
                if not isinstance(updates, list) or len(updates) > 20:
                    raise ApiError(400, "فهرست اصلاح زمان انجام‌دهندگان نامعتبر است.")
                editable = {p["person_id"] for p in people if p["specialty"] == user.specialty}
                selected, minutes = set(), {}
                for item in updates:
                    if not isinstance(item, dict) or set(item) != {"personId", "minutes"}:
                        raise ApiError(400, "فقط شناسهٔ فرد و دقیقهٔ مشارکت را وارد کنید.")
                    person_id = text(item, "personId", 60)
                    if person_id not in editable:
                        raise ApiError(403, "اصلاح زمان فقط برای افراد تخصص خودتان در همین گزارش مجاز است.")
                    if person_id in selected:
                        raise ApiError(400, "انجام‌دهنده در فهرست اصلاح تکراری است.")
                    selected.add(person_id)
                    minutes[person_id] = integer(item.get("minutes"))
                for person in candidate["participants"]:
                    if person["personId"] in minutes:
                        person["minutes"] = minutes[person["personId"]]
            if "parts" in payload:
                lines = payload["parts"]
                if not isinstance(lines, list) or len(lines) > 200 or any(
                    not isinstance(p, dict) or set(p) != {"partId", "quantity"} for p in lines):
                    raise ApiError(400, "فهرست کامل قطعات مصرفی معتبر نیست.")
                candidate["parts"] = deepcopy(lines)
            for key in ("startTime", "endTime"):
                if key in payload:
                    candidate[key] = text(payload, key, 5)
            # The endpoint cannot change assignment. A joint report may already
            # be assigned to another specialty; correcting one's own time does
            # not constitute a new assignment to that person.
            normalized = self.normalize(c, candidate, user, executed=True, preserve_assignment=True)
            if original.get("planId"):
                normalized["planId"] = original["planId"]
            self._preserve_pm(c, row, candidate, normalized, user, False)
            before, after = _values(original), _values(normalized)
            if before == after:
                return self._decision_in_transaction(c, user, order_id,
                    {"version": row["version"], "decision": "approve", "grades": payload.get("grades"), "reason": reason})
            if len(reason) < 3:
                raise ApiError(400, "علت اصلاح مدت کار یا مصرف قطعات را وارد کنید.")

            occurrence = c.one("SELECT * FROM pm_weekly_occurrences WHERE order_id=:id", {"id": order_id})
            base_archive = None
            if occurrence:
                base_archive = c.one("SELECT id,workflow_revision FROM pm_submission_archive WHERE order_id=:id AND workflow_revision=:rev",
                                     {"id": order_id, "rev": previous_revision})
                if base_archive is None:
                    raise ApiError(409, "نسخهٔ ارسالیِ بایگانی PM پیدا نشد؛ اصلاح بدون سابقهٔ معتبر مجاز نیست.")

            stamp, revision = now(), previous_revision + 1
            if c.execute("UPDATE orders SET body=:body,duration_minutes=:duration,workflow_revision=:rev,version=version+1,updated_at=:stamp "
                "WHERE id=:id AND version=:version AND status='supervisor'",
                {"body": dumps(normalized), "duration": normalized["durationMinutes"], "rev": revision,
                 "stamp": stamp, "id": order_id, "version": row["version"]}) != 1:
                raise ApiError(409, "گزارش هم‌زمان تغییر کرده است؛ نسخهٔ تازه را بررسی کنید.")
            self.save_lines(c, order_id, normalized)
            self._submission_policies(c, order_id, revision, stamp)
            if occurrence:
                from .pm_service import execution_end
                c.execute("UPDATE pm_weekly_occurrences SET executed_at=:ended,submitted_at=:stamp WHERE order_id=:id",
                          {"ended": execution_end(normalized), "stamp": stamp, "id": order_id})
            audit(c, user, "review-correction", "order", order_id,
                  {"reason": reason, "fromRevision": previous_revision, "toRevision": revision,
                   "before": before, "after": after, "invalidatedApprovals": False,
                   "approvalMode": "correct-and-approve"}, created_at=stamp)
            updated = c.one("SELECT * FROM orders WHERE id=:id", {"id": order_id})
            if occurrence:
                from .pm_archive import capture_submission
                capture_submission(c, updated, normalized, user, stamp,
                    correction={"baseArchiveId": base_archive["id"], "baseRevision": previous_revision, "reason": reason})
            self._carry_unchanged_supervisor_approvals(c, user, row, original, normalized, revision, stamp)
            return self._decision_in_transaction(c, user, order_id,
                {"version": updated["version"], "decision": "approve", "grades": payload.get("grades"), "reason": reason})

        return self.db.run_transaction(operation)
