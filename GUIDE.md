# Codex-Redteam-Mode User Guide

> A usage guide for complete beginners

## Project Installation

### Python Installation
This project relies on Python. You need to download Python from [python.org](https://www.python.org/).

![Step1](images/step1.png)


![Step2](images/step2.png)

### Install Dependencies
After downloading the project, go to the **project root directory** and run the following command:
```python
pip install -r requirements.txt
```

### Project Deployment
Before deploying the project, make sure you have the **ChatGPT desktop version** installed on your computer.

![Step3](images/step3.png)

> It is recommended to install **CC Switch** to manage various third-party APIs: [CCswitch.io](https://www.ccswitch.io/)

Finally, enter the following command in the project root directory to deploy the project:

```python
python scripts/install.py
```

## Prerequisite Knowledge for Using This Project (Recommended Reading)

### Agent, Skill, and LLM

#### LLM
Large Language Models, such as **GPT5.6** and **Opus5**, are used to understand and generate language.
> The capability of LLM: predicting the next word

#### Skill
Skills can be used to provide AI with specific domain knowledge or ideas, and can also limit the AI's operational scope. This project is a Skill.

#### Agent
**Codex App** and **Codex CLI** can plan task steps, call tools, and drive the project workflow.

### What You Should Know Before Penetration

#### Trusted Access for Cyber

For the following models, there is cybersecurity monitoring. A small model reviews inputs and outputs in the cloud. If a session is detected to be a cybersecurity attack, the session will be immediately disabled until the user passes **cybersecurity trust verification**.
```text
GPT-5.6
GPT-5.5
Opus4.7
Opus4.8
Opus5
Fable5
```

### About This Project
This project is a **Red Team penetration workflow**, including conventional web penetration, cryptography, internal network penetration, reverse engineering methods, and other content. It does not include workflows for other areas (such as cheating software, card game cheats, etc.).
