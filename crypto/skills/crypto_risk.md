# Crypto Risk Management Rules

## Position Limits
- Max single position: 30% of crypto capital
- Max total exposure: 90% of crypto capital
- Minimum cash reserve: 10% always

## Drawdown Circuit Breaker
- **Yellow zone (5%)**: Reduce position sizes by 50%
- **Red zone (10%)**: HALT all new trades, begin orderly liquidation
- Reset: Only resume after drawdown recovers to < 5%

## Correlation Controls
- BTC-correlated group (BTC, ETH): max 60% combined
- Alt-coin group (SOL, LINK, DOGE): max 40% combined
- If BTC drops > 5% in 1 hour, consider selling all positions

## Anti-Churn
- Minimum 5 minutes between trades on same pair
- Maximum 20 trades per day across all pairs
- If win rate drops below 40% over last 10 trades, pause for 30 minutes

## Slippage Monitoring
- Alert if slippage exceeds 50 bps on any trade
- If average slippage exceeds 30 bps over last 5 trades, reduce order sizes

## Risk Per Trade
- Default: 2% of capital at risk per trade
- Stop distance: 1.5x ATR
- Risk/Reward minimum: 1.5:1

## Daily Loss Limit
- If daily realized losses exceed 5% of capital, halt all trading until next day
- Send Telegram alert immediately on halt
