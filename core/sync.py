"""Data freshness rules; no GUI or OS dependencies."""
def identity(cfg):
    return tuple(str(cfg.get(k, '')) for k in
                 ('edu_code', 'school_code', 'school_kind', 'grade', 'classnm'))

def resolve_data(fetched, cached):
    """None means failure; [] means a successful empty response."""
    result, states = {}, {}
    for key, value in fetched.items():
        if value is None:
            prior = (cached or {}).get(key)
            result[key] = prior if prior is not None else []
            states[key] = 'cached' if prior is not None else 'unavailable'
        else:
            result[key] = value
            states[key] = 'fresh' if value else 'empty'
    result['states'] = states
    return result
