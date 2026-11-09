# ushabti

The SDK for [Ushabti](https://github.com/ushabti-io/ushabti), an open-source DevOps agent harness.

It holds the vocabulary every other package shares:

- `Spell`: the declarative definition of one ushabti (one agent instance).
- `Implement`: a tool an ushabti can use, either `read` or `write`.
- `Weighing` and `Verdict`: the safety gate every write implement must pass.
- `Papyrus`: the append-only audit log.
- `ModelProvider`: the model adapter protocol, with Anthropic, OpenAI-compatible, and fake providers.

```bash
pip install ushabti                 # core only
pip install "ushabti[anthropic]"    # with the Anthropic SDK
pip install "ushabti[openai]"       # with the OpenAI SDK, for any OpenAI-compatible endpoint
```

To run an agent, install `ushabti-agent`. To coordinate several, install `ushabti-overseer`.

See the repository README for the glossary, the architecture, and the safety defaults.
