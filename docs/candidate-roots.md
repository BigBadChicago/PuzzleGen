# Candidate roots and senses from oewn:2024

Measured against the committed exports and the 42 overlay words that have no entity in the current lexicon. Source: english-wordnet-2024.xml.gz, loaded into a local wn database.

## 1. Depth is not a lever

The nine roots that stop at level 6 were checked against the full WordNet tree. Synsets that sit deeper than level 6:

| root | total | within 6 | beyond 6 |
|---|---|---|---|
| bird | 871 | 865 | 6 |
| carnivore | 369 | 363 | 6 |
| fish | 617 | 516 | 101 |
| herb | 1042 | 1036 | 6 |
| insect | 454 | 454 | 0 |
| musical instrument | 170 | 170 | 0 |
| reptile | 297 | 284 | 13 |
| sport | 180 | 180 | 0 |
| vehicle | 547 | 544 | 3 |

Only `fish` loses anything meaningful (101 synsets, about 16 percent). Re-export it alone at depth 8. Every other root is complete at depth 6, so raising depth cannot recover any of the 42 absent words.

```
python tools/export_wordnet.py --root oewn-02514684-n --depth 8 \
    --out content/seeds/wordnet-fish.lexicon.json
```

## 2. Candidate roots, ranked by absent words recovered

`overlap` is the share of the root's tree already present in the committed exports, which is the lemma collision exposure. `recovers` counts absent words found anywhere under the root across all their noun senses, so some hits are the wrong meaning; section 4 shows each word's first sense.

| root | synset id | size | overlap | recovers |
|---|---|---|---|---|
| implement | oewn-03569147-n | 787 | 30% | 12: broom, hoe, chainsaw, comb, jigsaw, rake, drill, ram, cutlery, fork, knife, scissors |
| shrub | oewn-13133423-n | 783 | 1% | 9: broom, erica, heather, jasmine, cheese, cannabis, hemp, marijuana, indigo |
| tree | oewn-13124818-n | 1019 | 0% | 6: hazel, holly, coffee, mate, coconut, walnut |
| seafood | oewn-07792842-n | 121 | 0% | 5: clam, lobster, oyster, shrimp, coral |
| color | oewn-04963771-n | 148 | 0% | 5: hazel, heather, coffee, coral, indigo |
| cutting implement | oewn-03159112-n | 125 | 14% | 3: cutlery, knife, scissors |
| shellfish | oewn-07799186-n | 30 | 0% | 3: clam, lobster, oyster |
| mollusk | oewn-01943377-n | 86 | 0% | 3: clam, oyster, snail |
| seed | oewn-13156591-n | 76 | 0% | 3: coffee, coconut, walnut |
| power tool | oewn-04003842-n | 27 | 0% | 2: chainsaw, jigsaw |
| tableware | oewn-04389081-n | 91 | 1% | 2: cutlery, fork |
| crustacean | oewn-01977414-n | 66 | 0% | 2: lobster, shrimp |
| nut | oewn-13157333-n | 31 | 0% | 2: coconut, walnut |
| vine | oewn-13121436-n | 274 | 18% | 2: hops, hop |
| bulbous plant | oewn-13155061-n | 180 | 0% | 2: iris, onion |
| foodstuff | oewn-07581905-n | 582 | 47% | 2: barley, cheese |
| drug of abuse | oewn-03253661-n | 351 | 0% | 2: cannabis, marijuana |

## 3. Recommended batch

Chosen for yield per synset added and for low collision, not for raw coverage.

1. **color** (148 synsets, 0% overlap). Recovers `hazel`, `heather`, `coffee`, `coral`, `indigo` in their colour senses, which is the meaning `overlay.color` wants. This is the one hidden group that already works, so it is the best first test.
2. **mollusk** (86, 0%) and **crustacean** (66, 0%). These hold the animal senses of `clam`, `oyster`, `snail`, `lobster`, `shrimp` and `crab`, which is what `overlay.shell` means. The earlier `shellfish` recommendation was wrong: that root holds the food senses (the flesh of a clam), so it is dropped. List these two first in the build so `crab` takes the crustacean definition.
3. **cutting implement** (125, 14%), **tableware** (91, 1%), **power tool** (27, 0%). Together recover `cutlery`, `knife`, `scissors`, `fork`, `chainsaw`, `jigsaw` for the kitchen, blade and handle groups, with far less collision than `implement` (787, 30%).
4. **drug of abuse** (351, 0%). Recovers `cannabis`, `marijuana` for `overlay.smoked`.
5. **shrub** (783, 1%). Recovers 9 words (`broom`, `erica`, `heather`, `jasmine`, `cheese`, `cannabis`, `hemp`, `marijuana`, `indigo`) but is large and mostly obscure, so the frequency filter will thin it. Add last, and only if the earlier roots leave gaps.

Not recommended: `foodstuff` (47% overlap), `natural object`, `plant organ`, `invertebrate` (too broad, they add synsets and few usable parents).

`broom`, `hoe`, `rake`, `comb`, `drill` appear only under `implement`. If the handle group needs them, add `implement` as a fallback and run the collision report first.

```
python tools/export_wordnet.py --root oewn-04963771-n --depth 6 \
    --out content/seeds/wordnet-color.lexicon.json
python tools/export_wordnet.py --root oewn-07799186-n --depth 6 \
    --out content/seeds/wordnet-shellfish.lexicon.json
python tools/export_wordnet.py --root oewn-03159112-n --depth 6 \
    --out content/seeds/wordnet-cutting_implement.lexicon.json
python tools/export_wordnet.py --root oewn-04389081-n --depth 6 \
    --out content/seeds/wordnet-tableware.lexicon.json
python tools/export_wordnet.py --root oewn-04003842-n --depth 6 \
    --out content/seeds/wordnet-power_tool.lexicon.json
python tools/export_wordnet.py --root oewn-03253661-n --depth 6 \
    --out content/seeds/wordnet-drug_of_abuse.lexicon.json
```

After exporting: name every file explicitly in the build, never glob. Run `tools/report_lemma_collisions.py` over the full list and read the section on overlay words whose second meaning lost its parent before building.

## 4. First noun sense of each absent word

Review these. A wrong first sense means the word needs an explicit synset id in the overlay, not a new root.

| word | overlay groups | first sense id | meaning | parent |
|---|---|---|---|---|
| broom | handle | oewn-02910059-n | a cleaning implement for sweeping; bundle of straws or twigs attached  | cleaning implement |
| hoe | handle | oewn-03529754-n | a tool with a flat blade attached at right angles to a long handle | tool |
| erica | name | oewn-12247937-n | any plant of the genus Erica | heath |
| hazel | name | oewn-13164861-n | Australian tree grown especially for ornament and its fine-grained woo | tree |
| heather | name | oewn-12254046-n | common Old World heath represented by many varieties; low evergreen gr | heath |
| holly | name | oewn-11076649-n | United States rock star (1936-1959) | rock star, songwriter |
| iris | name | oewn-12432427-n | plants with sword-shaped leaves and erect stalks bearing bright-colore | iridaceous plant |
| barley | brewed | oewn-07819069-n | a grain of barley | grain |
| coffee | brewed | oewn-07945759-n | a beverage consisting of an infusion of ground coffee beans | beverage |
| hops | brewed | oewn-12418369-n | twining perennials having cordate leaves and flowers arranged in conel | vine |
| jasmine | brewed | oewn-12327234-n | any of several shrubs and vines of the genus Jasminum chiefly native t | shrub |
| mate | brewed | oewn-10319708-n | the officer below the master on a commercial ship | officer |
| beaver | teeth | oewn-09764560-n | a native or resident of Oregon | American |
| chainsaw | teeth | oewn-03004288-n | portable power saw; teeth linked to form an endless chain | power saw |
| comb | teeth | oewn-03078859-n | a flat device with narrow pointed teeth on one edge; disentangles or a | device |
| jigsaw | teeth | oewn-04128822-n | a portable power saw with a reciprocating blade; can be used with a va | power saw |
| rake | teeth | oewn-10525535-n | a dissolute man in fashionable society | libertine |
| rodent | teeth | oewn-02332053-n | relatively small placental mammals having a single pair of constantly  | placental |
| clam | shell | oewn-01959122-n | burrowing marine mollusk living on sand or mud; the shell closes with  | bivalve |
| coconut | shell | oewn-07789214-n | the edible white meat of a coconut; often shredded for use in e.g. cak | food |
| lobster | shell | oewn-07808701-n | flesh of a lobster | shellfish |
| oyster | shell | oewn-01963100-n | marine mollusks having a rough irregular shell; found on the sea bed m | bivalve |
| shrimp | shell | oewn-10563101-n | disparaging terms for small people | small person |
| snail | shell | oewn-01947031-n | freshwater or marine or terrestrial gastropod mollusk usually having a | gastropod |
| walnut | shell | oewn-07787188-n | nut of any of various walnut trees having a wrinkled two-lobed seed wi | edible nut |
| dock | verb | oewn-03221894-n | an enclosure in a court of law where the defendant sits during the tri | enclosure |
| drill | verb | oewn-03244429-n | a tool with a sharp point and cutting edges for making holes in hard m | tool |
| hop | verb | oewn-00120830-n | the act of hopping; jumping upward or forward (especially on one foot) | jump |
| poke | verb | oewn-11876022-n | tall coarse perennial American herb having small white flowers followe | pokeweed |
| ram | verb | oewn-04059869-n | the most common computer memory which can be used by programs to perfo | volatile storage |
| rush | verb | oewn-11294259-n | physician and American Revolutionary leader; signer of the Declaration | doctor, American Revolutionary leader |
| cheese | smoked,kitchen | oewn-07866305-n | a solid food prepared from the pressed curd of milk | dairy product, food |
| cutlery | kitchen | oewn-03158739-n | a cutting implement; a tool for cutting | cutting implement |
| fork | kitchen | oewn-03388794-n | a utensil with two or more prongs, used for serving or eating food | cutlery |
| knife | blade,kitchen | oewn-03628765-n | edge tool used as a cutting instrument; has a pointed blade with a sha | edge tool |
| onion | kitchen | oewn-12453586-n | the bulb of an onion plant | bulb |
| scissors | kitchen | oewn-04155119-n | an edge tool having two crossed pivoting blades | edge tool, compound lever |
| cannabis | smoked | oewn-12417441-n | any plant of the genus Cannabis; a coarse bushy annual with palmate le | shrub |
| hemp | smoked | oewn-14930651-n | a plant fiber | plant fiber |
| marijuana | smoked | oewn-12417727-n | a strong-smelling plant whose dried leaves can be smoked for a pleasan | cannabis |
| coral | color | oewn-04979087-n | a variable color averaging a deep pink | pink |
| indigo | color | oewn-14779039-n | a blue dye obtained from plants or made synthetically | dye |

## 5. The 22 overlay words still unsupplied after the roots above

Measured with the 16 current roots plus all seven pending roots. These need a second batch.

```
beaver broom cheese coconut comb drill erica hemp hoe holly hop hops
iris jasmine mate mower onion rake ram rodent rush walnut
```

| root | synset id | size | overlap by id | recovers |
|---|---|---|---|---|
| tool | oewn-04459089-n | 379 | 78% | comb, drill, hoe, mower, rake, ram |
| rodent | oewn-02332053-n | 139 | not measured | rodent, beaver (the animal; beaver's first sense is a person from Oregon) |
| dairy product | oewn-07859751-n | not measured | not measured | cheese |
| nut | oewn-13157333-n | 31 | 0% | coconut, walnut |
| bulbous plant | oewn-13155061-n | 180 | 0% | iris, onion |
| vine | oewn-13121436-n | 274 | 18% | hop, hops |
| heath | oewn-12247449-n | 15 | 0% | broom, erica |
| shrub | oewn-13133423-n | 783 | 1% | jasmine, hemp, broom, erica |
| angiospermous tree | oewn-13130492-n | 317 | 0% | holly, walnut |
| marsh plant | oewn-13143123-n | small | not measured | rush |

`tool` has high overlap because `hand tool` is already a root, but the overlap is by synset id, so the shared synsets merge into the same categories rather than colliding by name. It also supersedes the `implement` fallback suggested in section 3 for these six words.

`mate` (the beverage) is not among the first four noun senses of the word in OEWN, so no root reaches it from the sense the overlay means. It needs either the explicit synset id or removal from the overlay.

Before building this batch, run the fixed `tools/report_lemma_collisions.py` and read its refused parents section.
