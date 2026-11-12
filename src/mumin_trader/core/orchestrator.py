class Orchestrator:
    """Coordinates research components. Risk approval remains mandatory."""

    def __init__(self, scanner, decision_engine, risk_engine, fee_calculator):
        self.scanner = scanner
        self.decision_engine = decision_engine
        self.risk_engine = risk_engine
        self.fee_calculator = fee_calculator
