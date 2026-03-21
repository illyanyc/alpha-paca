# Crypto News Analysis Skill

## Role
Analyze cryptocurrency news articles to extract trading-relevant sentiment signals.

## Classification Schema

### Sentiment
- **bullish**: Positive for crypto prices (adoption, ETF approval, institutional buying, regulatory clarity)
- **bearish**: Negative for crypto prices (bans, hacks, exchange failures, regulatory crackdown, whale dumps)
- **neutral**: No clear directional impact (technical updates, minor partnerships, educational content)

### Urgency
- **high**: Requires immediate action (exchange halt, major hack, sudden regulation, flash crash, breaking institutional news)
- **medium**: Important but not time-critical (earnings reports, scheduled upgrades, gradual trend shifts)
- **low**: Background information (opinion pieces, long-term forecasts, minor partnerships)

### Affected Coins
Map each article to specific cryptocurrencies it impacts:
- Bitcoin-specific news -> BTC/USD
- Ethereum-specific news -> ETH/USD
- DeFi/smart contract news -> ETH/USD, SOL/USD, LINK/USD
- Meme coin news -> DOGE/USD
- Broad market news -> ALL pairs
- Regulatory news -> primarily BTC/USD, ETH/USD (largest market cap)

## Key Signals to Watch

### Strongly Bullish
- Bitcoin ETF inflows or approval
- Major institutional purchases (MicroStrategy, Tesla, etc.)
- Favorable regulatory decisions
- Network upgrades reducing fees or increasing throughput
- Declining exchange reserves (coins moving to cold storage)

### Strongly Bearish
- Exchange hacks or insolvency
- Government bans or restrictive regulation
- Large whale sell-offs (> $100M+)
- Critical security vulnerabilities
- USDT/USDC depeg fears

### Noise (Ignore)
- Price prediction articles with no fundamental basis
- Social media hype without institutional backing
- Repeated coverage of old news
- Promotional content disguised as news

## Overall Scoring
After analyzing all articles:
- Count bullish vs bearish articles, weighted by urgency
- Score from -1.0 (extremely bearish) to +1.0 (extremely bullish)
- List top 3-5 key events driving the sentiment
