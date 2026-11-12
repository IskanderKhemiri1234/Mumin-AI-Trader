from config.config import get_settings

if __name__ == "__main__":
    settings = get_settings()
    print(f"Configuration valid: environment={settings.TRADING_ENV.value}, mode={settings.TRADING_MODE.value}")
    print("Order submission: disabled in this foundation.")
