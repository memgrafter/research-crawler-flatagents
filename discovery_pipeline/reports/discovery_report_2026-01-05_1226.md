# Discovery Report - 2026-01-05_1226

**Pipeline Results:**
- New papers crawled: 0
- Papers scored: 0  
- Papers enriched: 0

---


## Executive Summary

This week's papers showcase significant advances in reinforcement learning optimization, multimodal model capabilities, and our understanding of emergent properties in large-scale models. A dominant theme is efficiency optimization - from ISOPO's elegant single-step natural gradient approximation to BandiK's innovative multi-bandit framework for task selection. Audio models particularly impress with MiMo-Audio demonstrating emergent few-shot learning at unprecedented scale (100M+ hours), while Recursive Language Models offer a clever inference-time solution to context window limitations through programmatic decomposition.

Several papers deepen our theoretical understanding, including the fascinating discovery of long-range dependencies in English text extending to 10,000 characters, challenging assumptions about context windows. Safety and robustness remain critical concerns, with novel defenses against model extraction attacks (DivQAT) and important insights on AI alignment requiring uncertainty reasoning. The continual learning perspective through computational embedding offers a fresh theoretical framework for understanding agent limitations. Collectively, these developments point toward more efficient, capable, and theoretically grounded AI systems.

## Top Papers (Detailed)

### 1. **[ISOPO: Proximal policy gradients without pi-old](https://arxiv.org/abs/2512.23353)**
- **Publication**: 2025-12-29 (Updated: 2025-12-30)
- **Topics**: Reinforcement Learning, Policy Optimization
- **What it does**: Introduces Isometric Policy Optimization (ISOPO), an efficient method that approximates natural policy gradients in a single gradient step by normalizing log-probability gradients in the Fisher metric, eliminating the need for multiple gradient steps and importance ratio clipping used in methods like GRPO or CISPO.
- **Implementation notes**: Can be implemented with negligible computational overhead compared to vanilla REINFORCE. Two variants: (1) simple normalization of log-probability gradients before contracting with advantages, (2) transforms microbatch advantages based on neural tangent kernel layer-wise in a single backward pass.
- **Why it matters**: Offers a computationally efficient alternative to proximal policy methods, potentially reducing training time and complexity while maintaining performance. The single-step approach could make natural policy gradients more accessible for large-scale applications.

### 2. **[CHARM: Considering Human Attributes for Reinforcement Modeling](https://arxiv.org/abs/2506.13079)**
- **Publication**: 2025-06-16 (Updated: 2025-12-29)
- **Topics**: Human-in-the-Loop RL, Human Factors
- **What it does**: Investigates how human feedback patterns in RLHF correlate with human characteristics beyond just task rewards. Conducted a study with 46 participants on two long-horizon tasks, finding that robot experience and educational background significantly affect feedback patterns.
- **Implementation notes**: Public space study with two long horizon tasks. All collected human feedback, characteristics, and code available at GitHub repository. Demonstrated improved prediction of human feedback value when incorporating human characteristics.
- **Why it matters**: Provides empirical evidence that human diversity matters in RLHF systems, potentially leading to more personalized and effective human-AI collaboration systems. The publicly available dataset and code enable further research in this direction.

### 3. **[Large language models and the entropy of English](https://arxiv.org/abs/2512.24969)**
- **Publication**: 2025-12-31
- **Topics**: Linguistics, Information Theory, LLM Analysis
- **What it does**: Uses LLMs to reveal long-range dependencies in English text, showing that conditional entropy continues decreasing with context length up to N~10^4 characters, implying direct dependencies across these distances and emergent certainty about increasing fractions of characters.
- **Implementation notes**: Analyzed texts from various sources using LLMs. Observed different dynamics at long vs. short context lengths during model training, suggesting gradual learning of long-range structure. Provides data-independent verification of correlations at these separations.
- **Why it matters**: Challenges assumptions about effective context window sizes and provides constraints for statistical physics models of language and LLMs. The discovery of such long-range dependencies has implications for model architecture and training strategies.

### 4. **[BandiK: Efficient Multi-Task Decomposition Using a Multi-Bandit Framework](https://arxiv.org/abs/2512.24708)**
- **Publication**: 2025-12-31
- **Topics**: Multi-Task Learning, Bandit Algorithms
- **What it does**: Proposes a three-stage method for selecting beneficial auxiliary task sets in multi-task learning using multi-bandits, addressing the high computational cost of evaluating candidate sets and reducing exponential possibilities to linear numbers.
- **Implementation notes**: Three-stage approach: (1) estimates pairwise transfers, (2) constructs linear number of candidate sets, (3) employs Multi-Armed Bandit framework with semi-overlapping arm property. Each arm pull evaluates candidates using single random train-test split.
- **Why it matters**: Provides a computationally efficient solution to the critical problem of task selection in multi-task learning and foundation models, potentially reducing negative transfer and improving knowledge transfer across tasks.

### 5. **[Recursive Language Models](https://arxiv.org/abs/2512.24601)**
- **Publication**: 2025-12-31
- **Topics**: Long Context, Inference-Time Scaling
- **What it does**: Introduces RLMs, an inference strategy allowing LLMs to process arbitrarily long prompts by treating them as external environments, enabling programmatic examination, decomposition, and recursive self-calling over prompt snippets.
- **Implementation notes**: Handles inputs up to two orders of magnitude beyond model context windows. Outperforms base LLMs and common long-context scaffolds on four diverse tasks with comparable or cheaper cost per query. Uses recursive calling mechanism.
- **Why it matters**: Offers a practical solution to context window limitations without requiring architectural changes or retraining. Could enable applications requiring processing of very long documents while maintaining model quality.

### 6. **[Characterization of Transfer Using Multi-task Learning Curves](https://arxiv.org/abs/2512.24866)**
- **Publication**: 2025-12-31
- **Topics**: Transfer Learning, Learning Curves
- **What it does**: Proposes using multi-task learning curves to characterize transfer effects by varying sample sizes rather than model perturbations, providing a more fundamental understanding of transfer phenomena in both training and inductive inference.
- **Implementation notes**: Efficient method to approximate multi-task learning curves analogous to Task Affinity Grouping. Evaluated on drug-target interaction dataset. Compares statistical vs. computational approaches to transfer.
- **Why it matters**: Offers a new perspective on understanding and quantifying transfer learning effects, potentially leading to better task selection and transfer strategies in foundation models and multi-task learning scenarios.

### 7. **[MiMo-Audio: Audio Language Models are Few-Shot Learners](https://arxiv.org/abs/2512.23808)**
- **Publication**: 2025-12-29
- **Topics**: Audio Models, Few-Shot Learning, Large-Scale Training
- **What it does**: Demonstrates that scaling audio language model pretraining to 100M+ hours enables emergent few-shot learning capabilities across diverse audio tasks, including speech intelligence, audio understanding, and generation tasks not
