CANDIDATE_LIMIT = 200
RESULT_LIMIT = 50


def evidence(comparisons, rules):
    return [{"field": field, "rule": rule, "weight": weight}
            for key, field, rule, weight in rules if comparisons[key]]


def result(row, confidence, comparisons, reasons):
    return {"id": str(row["id"]), "code": row["code"], "confidence": confidence,
            "comparisons": comparisons, "evidence": reasons}
