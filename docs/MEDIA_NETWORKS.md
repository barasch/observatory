# Media-attention networks: v1 method contract

**Implementation status:** Stage 2 adds a private candidate reservoir, shadow observer registry, RSS/Atom collector, normalization, within-observer mirror collapse, and health reporting. No clustering, inference, scoring, page, navigation link, schedule, active panel, or public data publication exists. See [MEDIA_COLLECTION.md](MEDIA_COLLECTION.md).

## Question and limit

The proposed Observatory tab asks: **what are empirically distinguishable media-attention networks paying attention to now?** It measures co-attention within a disclosed panel. It does not classify a person, program, outlet, or linked source as permanently belonging to a political tribe. An observer may have fractional membership in several networks, and those weights may change each week.

Until systematic coverage of major closed social platforms is available, the product must identify itself as an **open-media attention panel** and enumerate included and excluded media. It does not estimate total audience exposure.

## Four identities that must remain separate

| Identity | Meaning | Why it is separate |
| --- | --- | --- |
| Collection channel | One machine-readable endpoint, such as a website feed, podcast feed, or YouTube feed | Several channels can mirror one editorial product. They supply items but never independent agreement. |
| Observer | One editorial product whose acts of attention are measured | The podcast and video uploads of the same show collapse to one observer. |
| Brand family | Observers under common ownership or close coordination | Sibling products may remain separate observers when editorially distinct, but count once toward independent breadth. |
| Network lineage | One latent pattern of shared attention in a particular weekly model | It is recomputed from behavior and is never stored as a fixed property of an observer. |

The observer registry contains no ideology, party, network assignment, or presumed audience field. Unknown fields are rejected. Each observer must state the factual basis for treating it as a distinct editorial product. `brand_family_id` is required even for a self-owned, single-product observer so breadth has an explicit denominator.

## Panel construction

The candidate reservoir should begin with roughly 150–200 observers across websites, newsletters, podcasts, YouTube, Bluesky, and any later platform for which collection is lawful, reliable, and historically consistent. The initial active panel should begin near 100 and expand only while additional observers materially improve coverage or fit, with 150 as the initial ceiling.

An observer can be `candidate`, `shadow`, `active`, or `retired`. Candidates enter the shadow panel before promotion. Ordinary promotion and retirement occur monthly, require two consecutive weeks of evidence, and should not replace more than 10 percent of the active panel in a month. Dead or inaccessible feeds may be disabled immediately. Posting volume and follower count do not determine base weight.

Panel selection uses coverage, medium, information gain, and collection reliability. It must not balance a preconceived list of political categories.

## Observation and story-event rules

An observation records compact public metadata: observer, channel, medium, normalized title, URL, attention timestamp and its basis, story identifier, and cluster confidence. It contains no article body, transcript, private feedback, credential, or inferred ideology.

A **story event** is a time-bounded concrete development, independent of favorable or hostile framing. Different reactions to the same development remain one event; distinct developments involving the same person or topic remain separate events. Precision takes precedence over recall: uncertain items may remain separate rather than forcing a false merge.

Every retained observation belongs to exactly one story. Story time bounds equal the first and last constituent observations. An ISO-week manifest is assigned by `attention_at` in `America/New_York`; a trailing 24-hour snapshot can consequently cite two adjacent weekly manifests.

Repeated posts by one observer are retained for audit but reduce to the observer’s most recent qualifying timestamp for current scoring. Channels under the same observer cannot manufacture breadth. Observers in one brand family cannot manufacture independent-source eligibility.

## Weekly network reconstruction

Each Monday at 4:00 a.m. Eastern, the planned model will be reconstructed from the twelve preceding complete local calendar weeks. Observations receive exponential time decay with a four-week half-life:

\[
d(a)=2^{-a/4},
\]

where \(a\) is the observation age in weeks at the inference-window endpoint. Thus the preceding week has approximately full weight, observations four weeks old have one-half weight, and observations eight weeks old have one-quarter weight. The window begins and ends at Monday midnight in New York. It is defined in local calendar weeks rather than a fixed number of UTC hours, so daylight-saving transitions do not shift the boundary.

After mirror collapse and activity caps, a non-negative matrix factorization of the observer-by-story matrix supplies overlapping memberships. Common stories receive less inferential weight than stories that distinguish patterns of attention. No ideological label enters the model.

For observer \(i\), network \(g\), and model week \(t\), the membership weight is \(w_{igt}\), constrained by:

\[
w_{igt}\geq 0, \qquad \sum_g w_{igt}=1.
\]

The complete membership matrix is public. The weekly model is invalid if a row is incomplete, if a row does not sum to one within numerical tolerance, or if a nominal factor has zero membership mass.

### Participant measures

The participant preview uses only observers with usable data in the immediately preceding week. Its displayed **network share** is:

\[
p_{igt}=\frac{w_{igt}}{\sum_{j\in I_t}w_{jgt}},
\]

where \(I_t\) is that preceding week’s usable-observer set. The denominator is the network’s active modeled mass, not the participant’s posts or audience. Each observer’s own \(w_{igt}\) is separately displayed as **observer affinity**. The two numbers are not interchangeable.

The effective membership of network \(g\) is:

\[
N_{\mathrm{eff},g}=\frac{(\sum_i w_{ig})^2}{\sum_i w_{ig}^2}.
\]

This exposes a factor whose nominal membership is spread across many observers but whose mass is concentrated in very few.

## Network identity and lineage

Factor indexes are computational positions, not identities. Permuting otherwise identical factors must not change their public names. Each current network therefore has:

- an immutable internal lineage identifier;
- a neutral public label beginning with `Network A`, `Network B`, and so on; and
- a weekly version identifier of the form `lineage-0001@2026-W42`.

Successive factors are matched by optimal one-to-one assignment using cosine similarity of normalized participant-weight vectors. Story-profile similarity is a secondary tie-breaker. The provisional continuity threshold is 0.65; it must be calibrated against bootstrap variation before publication.

Lineage events use these rules:

- a continuation retains its predecessor’s lineage and letter;
- a birth receives the next unused lineage and letter;
- a retired letter is never recycled;
- exactly one child of a split—the closest—retains the parent lineage; other children receive new lineages;
- the closest parent of a merger supplies the successor lineage; all other parents are recorded; and
- an uncertain match receives a new lineage rather than fabricating continuity.

Every event records the relevant predecessor-successor similarities and shared membership mass. These links preserve the data required for a later alluvial history without requiring the map in v1.

## Trailing-24-hour attention

Current stories are scheduled for recomputation every three hours at 12:00, 3:00, 6:00, and 9:00 a.m. and p.m. Eastern. The interval is half-open: `start_at <= attention_at < end_at`. It always spans 24 elapsed hours, including across daylight-saving changes.

For observer \(i\), network \(g\), and story \(s\):

- \(w_{ig}\) is the latest successful weekly membership;
- \(a_{is}=1\) when the observer attended to the event in the window, otherwise zero;
- \(\Delta t_{is}\) is the age in hours of that observer’s most recent qualifying observation; and
- \(r_{is}=2^{-\Delta t_{is}/12}\) gives current attention a twelve-hour half-life.

The network score is:

\[
A_{gs}=\frac{\sum_i w_{ig}a_{is}r_{is}}{\sum_{i\in I_{24}}w_{ig}},
\]

where \(I_{24}\) contains only observers successfully checked for that run. Failed feeds are disclosed and excluded from the denominator; they are not treated as evidence of inattention. The panel score is the corresponding equal-observer mean over \(I_{24}\). The displayed over-index is \(A_{gs}\) divided by that panel score.

A story is eligible only after attention from at least three deduplicated observers and three independent brand families. Each observer contributes at most once. A network may publish zero to three eligible stories, and the same real event may properly appear in several networks. Representative links must be drawn from the disclosed evidence set. A linked publisher does not thereby become a member of the network.

## Choosing the number of networks

Ten is provisional. The pilot compared only 7, 10, and 13, making ten a reasonable development setting but not an empirically selected optimum.

Before launch, the same expanded shadow data must evaluate every integer from 5 through 15. If the best result lies at 5 or 15, the range expands; a search boundary cannot be declared optimal. Comparison uses held-out reconstruction error and its uncertainty, bootstrap stability, minimum and median effective membership, agenda distinctiveness, continuity, and sensitivity to single observers and brand families. The decision uses a constrained one-standard-error rule: select the smallest model statistically indistinguishable from the best that also passes stability and effective-size gates.

After launch, an ordinary eight-week review compares the current \(k\) with \(k-2\), \(k-1\), \(k+1\), and \(k+2\). A boundary winner, quality-gate failure, accumulating residual structure, material panel change, or split/merger evidence triggers the complete range. A change must win at the current weekly cutoff and the immediately preceding cutoff unless the incumbent fails an absolute gate.

## Publication and failure boundary

The media-network files are a separate public product. They neither read from nor write to private feedback and do not alter the daily primary-source edition contract.

All public documents use strict field allowlists, versioned schemas, explicit-offset timestamps, HTTPS URLs without embedded credentials, and atomic last-good publication. A collection or scoring failure leaves the preceding snapshot intact and exposes its age. A failed or unstable reconstruction leaves the prior weekly model in force. Material coverage gaps remain visible.

Stage 1 supplied the validator, documentation, and invented fixtures. Stage 2 populates only `candidate` and `shadow` registry states and writes collection artifacts to an ignored private work directory. Later stages must not weaken these contracts silently; a material change requires a schema-version or documented contract change.
