"""Executable application boundary: questions, evidence, decisions, audit."""

from sentinel.analytics.evidence import recommendation
from sentinel.analytics.forecast import forecast_from_result
from sentinel.analytics.risk import RiskEngine
from sentinel.analytics.what_if import demand_scenario
from sentinel.audit.logger import ActionGate, AuditLog
from sentinel.nlq.service import ask


class Console:
    def __init__(self, database, planner=None, retriever=None):
        self.database, self.planner, self.retriever = database, planner, retriever
        self.audit = AuditLog(database)
        self.gate = ActionGate(self.audit)
        self.risks = None

    def question(self, text):
        record = ask(self.database, text, self.planner, self.retriever)
        # Record blocked questions and failures as well as successful queries.
        self.audit.append("query", record)
        if record["status"] == "ok":
            try:
                self._analyze(record)
            except (KeyError, TypeError, ValueError) as exc:
                # A valid SELECT can still return unusable analysis inputs. Do not
                # expose a partial recommendation or lose the failure from replay.
                for key in ("risks", "forecast", "what_if", "recommendations"):
                    record.pop(key, None)
                record.update(
                    status="missing_information",
                    abstained=True,
                    failure_behavior=f"Invalid or incomplete analysis inputs ({type(exc).__name__}): {exc}. No recommendation was created.",
                )
            self.audit.append("analysis", record)
        return record

    def _analyze(self, record):
        from sentinel.nlq.planner import validate_plan

        output = validate_plan(record["model_output"])
        intent = output["intent"]
        query = record["query_result"]
        risk_kind = {
            "stockout": "stockout",
            "supplier_risk": "supplier_reliability",
            "shipments": "late_delivery",
        }.get(intent)
        if risk_kind:
            if self.risks is None:
                self.risks = RiskEngine().fit()
            record["risks"] = [self.risks.assess(risk_kind, row) for row in query["rows"]]
            record["recommendations"] = [recommendation(query, risk) for risk in record["risks"]]
        elif intent == "forecast":
            record["forecast"] = forecast_from_result(query)
            record["recommendations"] = [recommendation(query, forecast_output=record["forecast"])]
        else:
            record["recommendations"] = [recommendation(query)]
        if intent == "what_if":
            increase = output["parameters"]["increase"]
            record["what_if"] = [demand_scenario(row, increase) for row in query["rows"]]
