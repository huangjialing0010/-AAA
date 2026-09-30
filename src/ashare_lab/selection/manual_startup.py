"""模拟交易启动前检查；缺证拒绝，不产生交易。"""
from datetime import date


def startup_check(*, strategy_frozen, eligibility_verified, execution_connected,
                  snapshot_sealed, verified_data_cutoff, required_data_cutoff):
    required = date.fromisoformat(required_data_cutoff)
    reasons = []
    for flag, reason in (
        (strategy_frozen, 'EXECUTABLE_STRATEGY_NOT_FROZEN'),
        (eligibility_verified, 'FULL_QUALITY_AND_RISK_NOT_VERIFIED'),
        (execution_connected, 'EXECUTION_PIPELINE_NOT_CONNECTED'),
        (snapshot_sealed, 'PRICE_SNAPSHOT_NOT_SEALED'),
    ):
        if flag is not True:
            reasons.append(reason)
    if verified_data_cutoff is None:
        reasons.append('DATA_CUTOFF_NOT_VERIFIED')
    elif date.fromisoformat(verified_data_cutoff) != required:
        reasons.append('DATA_CUTOFF_MISMATCH')
    return {'status': 'BLOCKED' if reasons else 'PRECHECK_PASS_NOT_EXECUTED',
            'reasons': reasons, 'orders_created': 0, 'trades_created': 0}
