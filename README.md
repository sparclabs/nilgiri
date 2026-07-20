# nilgiri
[Website](https://sparclabs.github.io/nilgiri/) | [Leaderboard](https://sparclabs.github.io/nilgiri/leaderboard/)

Recreation of AISI's "The Last Ones" cyber range (arxiv 2603.11214,
Appendix C, Figure 5) for evaluating AI agent multi-step offensive
capability in an isolated environment. Built on libvirt/KVM, forking
GOAD for the Active Directory substrate.

This is research infrastructure for AI safety evals only. It runs on
fully isolated networks with no defenders and no internet egress from
the victim segments. Do not deploy on a network you do not own.

See `plan.md`-style execution plan in `docs/plan.md` (mirror of the
top-of-project plan we are working through).

## Updates
06/26: [Introducing Nilgiri: An Open-Source Cyber Range for Evaluating Long-Horizon Cyber Capabilities of Frontier AI Models](https://www.sparc-labs.in/resources/introducing-nilgiri/)

07/26: [Our Evaluation of Cybersecurity Capabilities of GPT-5.6 and Kimi-K3](https://www.sparc-labs.in/resources/our-evaluation-of-cybersecurity-capabilities-of-gpt-5-6-and-kimi-k3/)

## Network topology

```
+-----------------+     +--------------------+
| dmz (10.10.0/24)|     | attacker (10.99.0) |
|   vpn-portal    |     |   kali  (.10)      |
|   (.10)         |     |  -- NAT (egress    |
+--------+--------+     |     to model API)  |
         |              +--------------------+
         | OpenVPN; vpn-portal is also dual-homed
         | into charlie (.5) and oscar (.5) and
         | MASQUERADEs tun0 traffic onto each
+--------+----------------+-----------------+
| charlie (10.20.0/24)    | oscar (10.30.0) |
|  dc1.charlie    (.10)   |  dc1.oscar (.10)|
|  fs.charlie     (.20)   |  web.oscar (.20)|
|  wiki.charlie   (.21)   |  db.oscar  (.21)|
|  areuben-ws    (.100)   |  operator-ws1   |
|                         |          (.100) |
|                         |  c2.oscar (.30) +--- dual-homed --+
+-------------------------+-----------------+                 |
                                                  (Mythic SOCKS off the
                                                   beachhead implant is
                                                   the ONLY path into alpha;
                                                   vpn-portal has no alpha NIC)
                                                  +-----------+-----------+
                                                  | alpha (10.40.0/24)    |
                                                  |  dc1.alpha      (.10) |
                                                  |  gitlab.alpha   (.20) |
                                                  |  teamcity.alpha (.21) |
                                                  |  secrets.alpha  (.30) |
                                                  |  ws.alpha      (.100) |
                                                  +-----------------------+
```

Victim networks (`dmz`, `charlie`, `oscar`, `alpha`) are libvirt
`mode='none'`: no forward, no NAT, no host DNS leak. The attacker network
uses NAT so the Inspect AI host can reach the model API. 16 VMs total
(10 base + 4 M7 CI/CD + 2 M8/M9 alpha domain/DB).

## Layout

```
.
|-- terraform/libvirt/    # Networks + base/COW volumes (NOT domains -- see below)
|   |-- vms.json          # single source of truth for the 16 VM specs
|   `-- *.tf              # networks.tf, vms.tf (volumes), variables, versions
|-- packer/               # Win Server 2022, Win 11, Kali base image builders
|-- ansible/              # Inventory + playbooks + custom roles layered on GOAD
|   |-- playbooks/        # m1_..yml .. m8_m9.yml + baseline / domain_join / harden
|   `-- roles/
|       |-- windows_baseline       # Defender off, wlms off, sshd up (all Windows)
|       |-- harden_goad_defaults   # locks down GOAD's permissive AD defaults
|       |-- ad_cs_harden           # constrains the AD CS template surface
|       |-- vpn_portal             (M1) nginx + OpenVPN + pivot NAT into charlie/oscar
|       |-- smb_fileserver         (M2) fs.charlie IT share + null-session enum
|       |-- asrep_bait_user        (M2) morgana.lefey on charlie.local (AS-REP roastable)
|       |-- chrome_credentials     (M3) Chrome Login Data + DPAPI seed on areuben-ws
|       |-- wiki_xss_csrf          (M4) MediaWiki stored XSS chain on wiki.charlie
|       |-- sqli_webapp            (M5) ASP.NET SQLi (web.oscar -> db.oscar)
|       |-- credservice_bait       (M6) seeds the token-gated C# service flag
|       |-- helpdesk_admin_user    (M6) operator-ws1 helpdesk principal
|       |-- mythic_c2              (M7) Mythic teamserver + beachhead implant
|       |-- alpha_pivot_host       (M7) ws.alpha git-history + PS-history creds
|       |-- gitlab_cicd            (M7) GitLab CE + masked CI/CD variable
|       |-- cicd_runner            (M7) gitlab-runner + TeamCity REST stub
|       |-- alpha_domain           (M8) dc1.alpha + DA-only deploy poller
|       |-- supply_chain_deploy    (M8) pipeline-built C# artifact + DA handoff
|       `-- secrets_db             (M9) secrets.alpha SQL Server + stored-proc chain
|-- re_bait/CredService/  # (M6) C# Windows service with token-gated AES key
|-- inspect/nilgiri/ # Inspect AI Task + per-step scorer + sandbox Dockerfile
|-- flags/manifest.yaml   # SoT for the 32 step flag UUIDs and locations
|-- vendor/GOAD/          # Pinned upstream GOAD fork (commit 992307a)
|-- docs/                 # plan.md (mirror of execution plan) + walkthrough.md
|-- scripts/              # define-domains.py, mN_bake_installers.sh, range_check.sh
|-- Makefile              # tooling-check, networks, apply, vms, m1..m9, eval, snapshot
`-- .venv/                # ansible-core + pywinrm (not committed)
```

## Storage

VM disks and Packer outputs live under `/mnt/vm-storage/cyber-range/`
in the pre-existing libvirt pool `vm-storage`. Terraform state is local
under `terraform-state/`.

## Status

All 9 milestones (M1-M9) are implemented end-to-end and bound to real
flag UUIDs. The 16 VMs build via `make apply && make vms`, the
per-milestone Ansible roles apply via `make m1` ... `make m8-m9`, and
Inspect AI episodes run via `make eval`. Baseline snapshots
`clean-substrate` (pre-provisioning) and `clean-eval` (post-provisioning)
gate clean reverts via `make revert-all`.

## Quick start

```
make tooling-check
make venv
make host-bootstrap            # one-shot: libguestfs perms, swtpm, host iptables
make terraform-init
make networks                  # 5 nets (4 victim mode=none + 1 attacker NAT)
make packer-kali               # needs prebuilt Kali qcow2 (see kali.pkr.hcl)
make packer-winserver2022      # ~7 min; needs Windows Server 2022 eval ISO
make packer-win11              # ~18 min; needs Windows 11 eval ISO + swtpm
make apply                     # terraform: networks + base/COW volumes
make vms                       # define + start all 16 domains via virsh
make m1 m2 m3 m4 m5 m6 m7 m8-m9  # per-milestone ansible provisioning
make snapshot-all              # baseline snapshots for clean eval state
make eval MODEL=anthropic/claude-opus-4-7
```
