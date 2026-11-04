"""Full curve modeling + locked multi-event scoring (milestone 5). Not implemented yet.

Planned interface (see the milestone-3 design notes):
  ObservedEvent: ridge points, noise, class only (never a per-event t0, v or d).
  score_hypotheses(events, hypotheses, trackset, start_time_s, c, params):
    one global Δt shared by every event; one f0 per event, profiled out in closed
    form (f0 = Σ f·r / Σ r²); relative event timing comes only from track positions
    and propagation. Works for any number of events; any subset can be scored to
    compare against the two-event baseline.
"""
