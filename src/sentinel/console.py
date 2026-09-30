"""Executable application boundary: questions, evidence, decisions, audit."""
from sentinel.analytics.evidence import recommendation
from sentinel.analytics.risk import RiskEngine
from sentinel.analytics.what_if import demand_scenario
from sentinel.analytics.forecast import forecast_from_result
from sentinel.audit.logger import AuditLog, ActionGate
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
            output = record["model_output"]
            from sentinel.nlq.planner import validate_plan
            intent = validate_plan(output)["intent"]
            query = record["query_result"]
            risk_kind = {"stockout": "stockout", "supplier_risk": "supplier_reliability", "shipments": "late_delivery"}.get(intent)
            if risk_kind:
                if self.risks is None:
                    self.risks = RiskEngine().fit()
                record["risks"] = [self.risks.assess(risk_kind, row) for row in query["rows"]]
                record["recommendations"] = [recommendation(query, risk) for risk in record["risks"]]
            elif intent == "forecast":
                try:
                    record["forecast"] = forecast_from_result(query)
                    record["recommendations"] = [recommendation(query, forecast_output=record["forecast"])]
                except ValueError as exc:
                    record.update(status="missing_information", abstained=True, failure_behavior=str(exc))
            else:
                record["recommendations"] = [recommendation(query)]
            if intent == "what_if":
                increase = validate_plan(output)["parameters"]["increase"]
                record["what_if"] = [demand_scenario(row, increase) for row in query["rows"]]
            self.audit.append("analysis", record)
        return record
