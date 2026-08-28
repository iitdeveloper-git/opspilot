from opspilot.chatops.telegram.bot import create_bot_app
from opspilot.config import Settings


def test_bot_app_creation_and_command_registration():
    settings = Settings(
        telegram_bot_token="123456789:ABCdefGHIjklMNOpqrSTUvwxYZ",
        telegram_allowed_user_ids="111,222",
        auth_mode="production",
        server_name="test-server",
    )
    bot, dp = create_bot_app(settings)
    assert bot is not None
    assert dp is not None

    # Verify message handlers are registered
    message_handlers = dp.message.handlers
    assert len(message_handlers) > 0

    # Verify callback query handlers are registered for confirm/cancel
    callback_handlers = dp.callback_query.handlers
    assert len(callback_handlers) >= 2
