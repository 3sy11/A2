"""Model services must not register commands owned by the other protocol."""

from bollydog.bootstrap import Bootstrap


def test_chat_and_embedding_commands_are_isolated():
    bootstrap = Bootstrap(config='config/agent.toml')
    commands = bootstrap.services.registry.all_commands()

    assert 'model.chat.Generate' in commands
    assert 'model.chat.CountTokens' in commands
    assert 'model.embed.Embed' in commands
    for destination in (
        'model.chat.Embed',
        'model.embed.Generate',
        'model.embed.CountTokens',
        'model.embed.ModelCalled',
        'model.embed.ModelFailed',
    ):
        assert destination not in commands
