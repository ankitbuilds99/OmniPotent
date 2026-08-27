// Reading plan limits and savings off the pricing API rather than restating
// them in the UI. Both of the numbers below used to be guessed, and both
// guesses were wrong: the seat cap was compared against -1, a value the API
// never returns, and the annual saving was hardcoded to 17% when every plan
// GET /billing/plans describes actually saves ~15%.

/**
 * What `max_users` holds on a plan with no seat cap. GET /billing/plans returns
 * 999999 for enterprise — never -1 — so a `=== -1` test advertised
 * "999999 users".
 */
export const UNLIMITED_USERS = 999999;

export function describeUserLimit(maxUsers: number): string {
  if (maxUsers < 0 || maxUsers >= UNLIMITED_USERS) {
    return 'Unlimited users';
  }
  return `${maxUsers} user${maxUsers === 1 ? '' : 's'}`;
}

/**
 * The best annual saving on offer, for the "Save up to N%" badge beside the
 * monthly/annual toggle. Free plans report 0 and are not an offer, so they are
 * excluded; with nothing to advertise this returns 0 and the badge is hidden.
 */
export function bestAnnualSavingsPercent(
  plans: readonly { annual_savings_percent: number }[]
): number {
  const offered = plans
    .map((p) => p.annual_savings_percent)
    .filter((percent) => percent > 0);
  return offered.length > 0 ? Math.max(...offered) : 0;
}
