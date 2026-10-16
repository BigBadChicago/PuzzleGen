# Open questions

Decisions the platform documents are waiting on. Each states what the question
actually is, what the options cost, and the recommendation. None of them blocks
reading the documents; all of them block finishing something.

Updated 2026-10-16.

## 1. How many cartridges can a player hold at once?

The slot cap is the player's side of the limit, and it is a different number
from `DEFAULT_MAX_ACTIVE = 4`, which is the operator's generation budget.

Options: **3**, enough for a habit and forces real choice. **5**, matches the
client's cached day window and leaves a spare slot. **Unlimited**, which makes
the catalogue the only limit.

Costs: a small cap makes the swap feature meaningful and makes every addition a
decision, which is the feeling the cartridge metaphor promises. It also means a
player who likes six games is permanently annoyed. Unlimited makes the daily
screen a chore and removes the one product mechanic that is uniquely this
platform's.

Recommendation: **5**, with the cap as a platform constant that can be raised
without a migration. Raising a cap is painless; lowering one takes something
away from everybody.

## 2. May a cached future day be readable before its day key?

The client caches two days ahead so a player with no signal still has today's
puzzle. A cached future day sitting decrypted on the device is tomorrow's answer
in somebody's pocket.

Options: **do not cache ahead**, which is simplest and loses the offline
guarantee for a player who does not open the app daily. **Cache encrypted**,
with the key released on the day, which keeps the guarantee and needs a key
release path that itself needs connectivity. **Cache ahead and accept it**,
treating early reading as a niche self spoiling act.

Costs: the second option is the only one that keeps both properties, and its
failure mode is that a player offline across a day boundary cannot unlock the
day they already have. Mitigation is a short lived key covering the whole cached
window, which weakens it toward the third option.

Recommendation: **cache encrypted**, with keys for the whole window delivered on
each refresh. It keeps the offline promise, raises the cost of early reading
above casual, and does not pretend to defeat a determined person with their own
device.

## 3. What happens when two histories belong to one person?

`MergeRequired` is raised today rather than guessing, which is correct and is
also a dead end for the player who hit it. The usual cause is one person who
played anonymously on two devices, and both histories are real.

Options: **keep the longer streak and discard the other**, simple and silently
destroys somebody's play. **Keep both, union the played days**, which is
truthful and makes a streak calculation that has to decide what two sessions on
one day key mean. **Let the player choose**, which is honest and is a support
flow in a product with no support.

Recommendation: **union the played days, and keep both sessions**, with the
streak computed over the union and the duplicate day resolved in the player's
favour. Nothing is discarded, and the rule is statable in one sentence to a
player.

## 4. Is the app directed to children?

A daily puzzle app has child players whether or not it is aimed at them. The
answer changes what sign in may do, whether an age gate is required, and which
regulations apply in each target market.

Options: **declare general audience and collect nothing age related**, which is
close to the current design. **Declare directed to children**, which brings
stricter obligations and in several markets forbids the provider sign in that
account merge assumes. **Age gate at first launch**, which is friction on the
first screen and is the only way to behave differently per age.

Recommendation: **general audience, with the privacy posture kept strict enough
that the distinction mostly does not matter**, and a lawyer consulted before the
first store submission rather than after. This is the one question in this list
where a wrong guess is expensive and legal, not an engineering preference.

## 5. Is there a business model?

There is none today, which is why `developer-terms.md` has no revenue clause and
the requirements have no purchase mechanism.

Options: **free, no monetisation**, which is honest for a project with one
maintainer. **Paid cartridges**, which makes the author relationship a
commercial one and needs every undecided clause in the developer terms answered.
**Subscription for more slots**, which turns the slot cap from a design choice
into a paywall.

Recommendation: **free for now, and decide before the first outside cartridge is
certified**, because an author who ships under free terms and is later asked to
accept commercial ones has a real grievance. The documents are written so a
decision drops in rather than requiring a rewrite.

## 6. How does a player learn their day was retracted?

Retraction restores the streak correctly and tells nobody. A player who sees
yesterday's result change and gets no explanation reasonably concludes the app
lost their data.

Options: **in app notice on next open**, which needs no push infrastructure and
reaches everybody who comes back. **Push notification**, which reaches people
immediately and needs a permission the app currently does not ask for. **Say
nothing**, which is the current behaviour.

Recommendation: **in app notice on next open**, carrying the reason, with no
push. A retraction is not urgent to the player; it is only confusing if
unexplained.

## 7. How long are server logs kept?

Session records are kept indefinitely on purpose, because they are the player's
history. Logs are a separate question with no answer written down.

Options: **thirty days**, long enough to debug a reported problem. **Ninety
days**, long enough to see a seasonal pattern. **Indefinite**, which is a
growing pile of personal data nobody decided to collect.

Recommendation: **thirty days**, with anything worth keeping longer promoted
into a record rather than left in a log.

## 8. May a third party cartridge ever ship a curated content file?

The rule today is that external content arrives as a reproducible declaration,
not a file, and the rule was chosen because it can be relaxed later without
anything being withdrawn, which is not true in the other direction.

Options: **never**, keeping the property absolute. **With review**, allowing a
file a curator has read. **Allow it when the game cannot be expressed as a
declaration**, which is the case that will actually arise.

Recommendation: **keep the rule, and treat the first genuine exception as a
request for a new platform generator**. The exception is evidence of a missing
generator, and building the generator serves every later game while an approved
file serves one.
