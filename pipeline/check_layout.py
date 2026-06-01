import pandas as pd

df = pd.read_excel("data/store_layout.xlsx")

print(df.head())
print()
print(df.columns.tolist())