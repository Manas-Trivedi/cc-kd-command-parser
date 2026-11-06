# Subnet plan for 10.20.0.0/16

Rule: a subnet with prefix `/p` has `2^(32-p)` addresses and `2^(32-p) - 2` usable hosts
(the first address is the network, the last is the broadcast).
Subnets are allocated largest first, starting at 10.20.0.0, each placed right after the previous one,
with the smallest prefix that fits.

## Result

| Subnet     | Hosts needed | Prefix | Network address | Usable host range          | Broadcast address | Usable hosts |
|------------|-------------:|:------:|-----------------|----------------------------|-------------------|-------------:|
| app        | 1,000        | /22    | 10.20.0.0       | 10.20.0.1 – 10.20.3.254    | 10.20.3.255       | 1,022        |
| public     | 250          | /24    | 10.20.4.0       | 10.20.4.1 – 10.20.4.254    | 10.20.4.255       | 254          |
| data       | 100          | /25    | 10.20.5.0       | 10.20.5.1 – 10.20.5.126    | 10.20.5.127       | 126          |
| management | 20           | /27    | 10.20.5.128     | 10.20.5.129 – 10.20.5.158  | 10.20.5.159       | 30           |

Next free address after the four subnets: 10.20.5.160. The rest of the /16 (10.20.5.160 – 10.20.255.255) is unallocated.

## Working

**1. Smallest prefix for each requirement** (need `2^(32-p) - 2 >= hosts`):

| Subnet     | Hosts | Try smaller block               | Chosen block                  | Prefix |
|------------|------:|---------------------------------|-------------------------------|:------:|
| app        | 1,000 | 2^9 - 2 = 510 (too small)       | 2^10 - 2 = 1,022              | /22    |
| public     | 250   | 2^7 - 2 = 126 (too small)       | 2^8 - 2 = 254                 | /24    |
| data       | 100   | 2^6 - 2 = 62 (too small)        | 2^7 - 2 = 126                 | /25    |
| management | 20    | 2^4 - 2 = 14 (too small)        | 2^5 - 2 = 30                  | /27    |

**2. Place them one after another.**

- **app /22** = 1,024 addresses = 4 blocks of 256. Starts at 10.20.0.0, covers 10.20.0.0 – 10.20.3.255.
  Network 10.20.0.0, broadcast 10.20.3.255, hosts 10.20.0.1 – 10.20.3.254.
- **public /24** = 256 addresses. Starts right after app at 10.20.4.0 (a multiple of 256, so aligned).
  Covers 10.20.4.0 – 10.20.4.255.
- **data /25** = 128 addresses. Starts at 10.20.5.0 (a multiple of 128, aligned). Covers 10.20.5.0 – 10.20.5.127.
- **management /27** = 32 addresses. Starts at 10.20.5.128 (128 is a multiple of 32, aligned).
  Covers 10.20.5.128 – 10.20.5.159.

Because the blocks go from largest to smallest, every next block starts on a boundary that is
a multiple of its own size, so no addresses are wasted between them.

**3. Check with Python** (`ipaddress` module):

```
>>> import ipaddress
>>> for c in ["10.20.0.0/22", "10.20.4.0/24", "10.20.5.0/25", "10.20.5.128/27"]:
...     n = ipaddress.ip_network(c); h = list(n.hosts())
...     print(c, h[0], h[-1], n.broadcast_address, n.num_addresses - 2)
10.20.0.0/22 10.20.0.1 10.20.3.254 10.20.3.255 1022
10.20.4.0/24 10.20.4.1 10.20.4.254 10.20.4.255 254
10.20.5.0/25 10.20.5.1 10.20.5.126 10.20.5.127 126
10.20.5.128/27 10.20.5.129 10.20.5.158 10.20.5.159 30
```

## NACL for the public subnet (`nacl.json`)

The load balancer lives in **public** (10.20.4.0/24). Clients use ephemeral ports 1024–65535.

| Dir | Rule | Protocol | Ports        | CIDR             | Action | Why |
|-----|-----:|----------|--------------|------------------|--------|-----|
| in  | 90   | all      | all          | 198.51.100.0/24  | deny   | abusive range, blocked even on 80/443 |
| in  | 100  | tcp      | 80           | 0.0.0.0/0        | allow  | HTTP |
| in  | 110  | tcp      | 443          | 0.0.0.0/0        | allow  | HTTPS |
| in  | 120  | tcp      | 22           | 10.20.5.128/27   | allow  | SSH from management only |
| in  | 130  | tcp      | 1024–65535   | 10.20.0.0/22     | allow  | replies from app back to the LB's ephemeral port |
| in  | *    | all      | all          | 0.0.0.0/0        | deny   | default (nothing matched) |
| out | 100  | tcp      | 8000         | 10.20.0.0/22     | allow  | LB → app service |
| out | 110  | tcp      | 1024–65535   | 0.0.0.0/0        | allow  | replies to clients' ephemeral ports |
| out | *    | all      | all          | 0.0.0.0/0        | deny   | default |

### Why replies need their own rules, and why the order matters

A NACL is **stateless**: it does not remember that a connection was opened. Every packet,
including the reply to a request we allowed, is checked against the rules on its own. When a
client connects to port 443, the reply leaves the load balancer *to the client's ephemeral port*
(1024–65535), so the outbound side needs rule 110 or the reply is dropped by the default deny.
In the same way, when the load balancer calls the app on port 8000, the app's reply comes back
*to the load balancer's ephemeral port*, which needs inbound rule 130. (A stateful security group
would let replies through automatically.)

Rules are checked from the **lowest number up and the first match wins**. A packet from
198.51.100.9 to port 443 matches both "deny 198.51.100.0/24" and "allow 443 from 0.0.0.0/0".
The deny only works because its number (90) is lower than the allow rules (100, 110); if it were,
say, rule 200, the 443 allow would match first and the abusive client would get in.
