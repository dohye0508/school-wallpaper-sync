"""Data freshness rules; no GUI or OS dependencies."""
def identity(cfg):
    return tuple(str(cfg.get(k, '')) for k in
                 ('edu_code', 'school_code', 'school_kind', 'grade', 'classnm'))

def resolve_data(fetched, cached):
    """None means failure; [] means a successful empty response."""
    result, states = {}, {}
    cached_states = (cached or {}).get('states')
    if not isinstance(cached_states, dict):
        cached_states = {}
    for key, value in fetched.items():
        if value is None:
            prior = (cached or {}).get(key)
            if cached_states.get(key) == 'unavailable':
                prior = None  # A recorded failure is not a successful empty cache.
            result[key] = prior if prior is not None else []
            states[key] = 'cached' if prior is not None else 'unavailable'
        else:
            result[key] = value
            states[key] = 'fresh' if value else 'empty'
    result['states'] = states
    return result
