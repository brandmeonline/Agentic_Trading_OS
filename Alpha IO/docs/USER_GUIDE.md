# User Guide (ELI5)

This system:
- Reads real-world trading signals (tweets, news)
- Tries to guess if they are valuable (confidence + memory + rarity)
- If valuable, it decides the best way to act:
  - Should we long it?
  - Should we spread trade it?
  - Should we ignore it?
- It then places a trade (or simulates it)
- After the trade plays out, it remembers what worked
- It keeps adjusting based on how it's doing

You can:
- View signals and votes in real time
- Visualize how the brain is working via intent graph
- See how similar the current signal is to past wins
- Let it train itself over and over

It’s like a self-evolving alpha hunter.