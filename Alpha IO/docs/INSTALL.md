# Install Instructions

### Requirements
- Python 3.9+
- OpenAI API Key
- Infura or Alchemy URL (for Ethereum access)

### Steps
1. Clone the repo
2. Install dependencies:
```bash
pip install -r requirements.txt
```
3. Copy and fill in `.env.template`:
```bash
cp .env.template .env
```
4. Run backtest to populate memory:
```bash
python tests/backtest.py
```
5. Launch Streamlit dashboard:
```bash
streamlit run dashboard/app.py
```