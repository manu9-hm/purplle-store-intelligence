import pandas as pd

df = pd.read_csv("data/pos_transactions.csv")

print(df.columns.tolist())
print(df.head())