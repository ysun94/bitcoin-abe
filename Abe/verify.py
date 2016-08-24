#!/usr/bin/env python
# Prototype database validation script.  Same args as abe.py.

# Copyright(C) 2011,2014 by Abe developers.

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Affero General Public License for more details.
# 
# You should have received a copy of the GNU Affero General Public
# License along with this program.  If not, see
# <http://www.gnu.org/licenses/agpl.html>.

import sys
import getopt
import DataStore
import util
import logging

# Default list of block statistics to check. Some are disabled due to
# common rounding errors
BLOCK_STATS_LIST = [
    'value_in',
    'value_out',
    'total_satoshis',
    'total_seconds',
    'satoshi_seconds',
    'total_ss',
    'ss_destroyed',
]
BLOCK_STATS_DISABLED = [
    'total_satoshis',
    'ss_destroyed',
]
BLOCK_STATS_DEFAULT = [i for i in BLOCK_STATS_LIST
                       if i not in BLOCK_STATS_DISABLED]


class AbeVerify:
    def __init__(self, store, logger, block_min, block_max, repair):
        self.store = store
        self.logger = logger
        self.block_min = block_min
        self.block_max = block_max
        self.repair = repair

    def verify_tx_merkle_hashes(self, chain_id, chain):
        checked, bad = 0, 0
        params = (chain_id,)
        if self.block_min is not None:
            params += (self.block_min,)
        if self.block_max is not None:
            params += (self.block_max,)

        for block_id, block_height, merkle_root, num_tx \
            in self.store.selectall("""
            SELECT b.block_id, b.block_height,
                   b.block_hashMerkleRoot, b.block_num_tx
              FROM block b
              JOIN chain_candidate cc ON (b.block_id = cc.block_id)
             WHERE cc.chain_id = ?""" + (
            "" if self.block_min is None else """ AND
              b.block_height >= ?""") + (
            "" if self.block_max is None else """ AND
              b.block_height <= ?"""), params):
            merkle_root = self.store.hashout(merkle_root)
            tree = []
            for (tx_hash,) in self.store.selectall("""
                SELECT tx.tx_hash
                  FROM block_tx bt
                  JOIN tx ON (bt.tx_id = tx.tx_id)
                 WHERE bt.block_id = ?
                 ORDER BY bt.tx_pos""", (block_id,)):
                tree.append(self.store.hashout(tx_hash))
            if len(tree) != num_tx:
                self.logger.warning("block %d (height %s): block_num_tx=%d"
                    "but found %d", block_id, block_height, num_tx, len(tree))
            root = chain.merkle_root(tree) or util.NULL_HASH
            if root != merkle_root:
                self.logger.error("block %d (height %s): block_hashMerkleRoot"
                    "mismatch.", block_id, block_height)
                bad += 1
            checked += 1
            if checked % 1000 == 0:
                self.logger.info("%d Merkle trees, %d bad", checked, bad)
        if checked % 1000 > 0:
            self.logger.info("%d Merkle trees, %d bad", checked, bad)
        return checked, bad

    def verify_block_stats(self, chain_id, stats):
        checked, bad = 0, 0
        params = (chain_id,)
        if self.block_min is not None:
            params += (self.block_min,)
        if self.block_max is not None:
            params += (self.block_max,)

        for block_id, in self.store.selectall("""
            SELECT b.block_id
              FROM block b
              JOIN chain_candidate cc ON (b.block_id = cc.block_id)
              JOIN block prev         ON (b.prev_block_id = prev.block_id)
            WHERE cc.chain_id = ?""" + (
            "" if self.block_min is None else """ AND
              b.block_height >= ?""") + (
            "" if self.block_max is None else """ AND
              b.block_height <= ?""") + """
            ORDER BY b.block_height ASC, b.block_id ASC""", params):

            block_height, nTime, value_in, value_out, total_satoshis, \
            total_seconds, satoshi_seconds, total_ss, ss_destroyed, \
            prev_nTime, prev_satoshis, prev_seconds, prev_ss, \
            prev_total_ss = self.store.selectrow("""
                SELECT b.block_height, b.block_nTime, b.block_value_in,
                       b.block_value_out, b.block_total_satoshis,
                       b.block_total_seconds, b.block_satoshi_seconds,
                       b.block_total_ss, b.block_ss_destroyed,
                       prev.block_nTime, prev.block_total_satoshis,
                       prev.block_total_seconds, prev.block_satoshi_seconds,
                       prev.block_total_ss
                  FROM block b
                  JOIN block prev ON (b.prev_block_id = prev.block_id)
                 WHERE b.block_id = ?""", (block_id,))

            if self.repair and None in (prev_satoshis, prev_seconds,
                                   prev_ss, prev_total_ss):
                raise Exception("Repair with broken prev block, dazed and "
                    "confused... block %s (height %s): %s" % (
                    block_id, block_height, str((prev_satoshis, prev_seconds,
                                                prev_ss, prev_total_ss))))

            # A dict makes easier comparison
            d = {
                'value_in': value_in,
                'value_out': value_out,
                'total_satoshis': total_satoshis,
                'total_seconds': total_seconds,
                'satoshi_seconds': satoshi_seconds,
                'total_ss': total_ss,
                'ss_destroyed': ss_destroyed
            }

            b = dict()
            b['value_in'], = self.store.selectrow("""
                SELECT COALESCE(value_sum, 0)
                  FROM chain c LEFT JOIN (
                    SELECT cc.chain_id, SUM(txout.txout_value) value_sum
                      FROM txout
                      JOIN txin             ON (txin.txout_id = txout.txout_id)
                      JOIN block_tx         ON (block_tx.tx_id = txin.tx_id)
                      JOIN block b          ON (b.block_id = block_tx.block_id)
                      JOIN chain_candidate cc ON (cc.block_id = b.block_id)
                    WHERE
                      cc.chain_id = ? AND
                      b.block_id = ?
                    GROUP BY cc.chain_id
                  ) a ON (c.chain_id = a.chain_id)
                WHERE c.chain_id = ?""", (chain_id, block_id, chain_id))
            b['value_in'] = (b['value_in'] if b['value_in'] else 0)

            b['value_out'], = self.store.selectrow("""
                SELECT COALESCE(value_sum, 0)
                  FROM chain c LEFT JOIN (
                    SELECT cc.chain_id, SUM(txout.txout_value) value_sum
                      FROM txout
                      JOIN block_tx           ON (block_tx.tx_id = txout.tx_id)
                      JOIN block b            ON (b.block_id = block_tx.block_id)
                      JOIN chain_candidate cc ON (cc.block_id = b.block_id)
                    WHERE
                      cc.chain_id = ? AND
                      b.block_id = ?
                    GROUP BY cc.chain_id
                  ) a ON (c.chain_id = a.chain_id)
                 WHERE c.chain_id = ?""", (chain_id, block_id, chain_id))
            b['value_out'] = (b['value_out'] if b['value_out'] else 0)

            b['total_seconds'] = prev_seconds + nTime - prev_nTime
            ss_created = prev_satoshis * (nTime - prev_nTime)
            b['total_ss'] = prev_total_ss + ss_created

            tx_ids = map(
                lambda row: row[0],
                self.store.selectall("""
                    SELECT tx_id
                      FROM block_tx
                     WHERE block_id = ?""", (block_id,)))
            b['ss_destroyed'] = \
                self.store._get_block_ss_destroyed(block_id, nTime, tx_ids)
            b['satoshi_seconds'] = prev_ss + ss_created - b['ss_destroyed']

            value_destroyed = 0
            for tid in tx_ids:
                destroyed, = self.store.selectrow("""
                    SELECT SUM(txout.txout_value) - SUM(
                        CASE WHEN txout.pubkey_id > 0 THEN txout.txout_value
                             ELSE 0 END)
                      FROM tx
                      LEFT JOIN txout ON (tx.tx_id = txout.tx_id)
                     WHERE tx.tx_id = ?""", (tid,))
                value_destroyed += destroyed

            b['total_satoshis'] = prev_satoshis + b['value_out'] \
                                  - b['value_in'] - value_destroyed

            if None in b.keys():
                raise Exception("Stats computation error: block %s (height %s): "
                                "%s" % (block_id, block_height, str(b)))

            # Finally... Check stats values match between d and b
            badcheck = False
            for key in stats:
                if d[key] != b[key]:
                        badcheck = True
                        self.logger.info("block %s (height %s): %s do not match: %s"
                                    " (should be %s)" % (block_id, block_height,
                                                         key, d[key], b[key]))
            checked += 1
            if badcheck and self.repair:
                self.store.sql("""
                    UPDATE block
                       SET block_value_in = ?,
                           block_value_out = ?,
                           block_total_seconds = ?,
                           block_total_satoshis = ?,
                           block_satoshi_seconds = ?,
                           block_total_ss = ?,
                           block_ss_destroyed = ?
                     WHERE block_id = ?""",
                          (self.store.intin(b['value_in']),
                           self.store.intin(b['value_out']),
                           self.store.intin(b['total_seconds']),
                           self.store.intin(b['total_satoshis']),
                           self.store.intin(b['satoshi_seconds']),
                           self.store.intin(b['total_ss']),
                           self.store.intin(b['ss_destroyed']),
                           block_id))
                self.logger.info("block %s (height %s): repaired" % (
                    block_id, block_height))

            if badcheck:
                bad += 1
            if checked % 1000 == 0:
                if self.repair:
                    self.store.commit()
                self.logger.info("%d Block stats, %d bad", checked, bad)
        if checked % 1000 > 0:
            if self.repair:
                self.store.commit()
            self.logger.info("%d block stats, %d bad", checked, bad)
        return checked, bad

def main(argv):
    cmdline = util.CmdLine(argv)
    cmdline.usage = lambda: \
        """Usage: verify.py --dbtype=MODULE --connect-args=ARGS [checks]

  Check database consistency

  Checks:
    --check-all     Check everything (overrides all other check options)
    --merkle-roots  Check merkle root hashes against block's transaction
    --block-stats   Check block statistics computed from block's transactions

  Options (can be combined):
    --min-height N  Check only blocks starting at height N
    --max-height N  Stop checking blocks above height N
    --blkstats LIST Comma-separated list of block statistics to check
                    Default:
                      """ + ','.join(BLOCK_STATS_DEFAULT) + """
                    Valid values:
                      """ + ','.join(BLOCK_STATS_LIST) + """
    --repair        Attempt to repair the database (not all checks support
                    repair)

  Warning: Some checks rely on previous blocks to have valid information.
   Testing from a specific height does not guarantee the previous blocks are
   valid and while the computed data may be relatively valid the whole thing
   could still be totally off.
"""

    store, argv = cmdline.init()
    if store is None:
        return 0

    try:
        opts, args = getopt.getopt(argv, "", [
            'check-all',
            'merkle-roots',
            'block-stats',
            'min-height=',
            'max-height=',
            'blkstats=',
            'repair',
        ])
    except getopt.GetoptError as e:
        print e.msg, "\n\n", cmdline.usage()
        return 1

    ckmerkle, ckstats = False, False
    min_height, max_height = None, None
    blkstats = BLOCK_STATS_DEFAULT
    repair = False
    for opt, arg in opts:
        if opt == '--check-all':
            ckmerkle, ckstats = True, True
        if opt == '--merkle-roots':
            ckmerkle = True
        if opt == '--block-stats':
            ckstats = True
        if opt == '--min-height':
            min_height = arg
        if opt == '--max-height':
            max_height = arg
        if opt == '--blkstats':
            blkstats = arg.split(',')
        if opt == '--repair':
            repair = True

    if args:
        print "Extra argument: %s!\n\n" % args[0], cmdline.usage()
        return 1

    if not True in (ckmerkle, ckstats):
        print "No checks selected!\n\n", cmdline.usage()
        return 1

    logger = logging.getLogger("verify")
    chk = AbeVerify(store, logger, min_height, max_height, repair)

    mchecked, mbad = 0, 0
    schecked, sbad = 0, 0
    for (chain_id,) in store.selectall("""
        SELECT chain_id FROM chain"""):
        logger.info("checking chain %d", chain_id)
        chain = store.chains_by.id[chain_id]
        if ckmerkle:
            mchecked1, mbad1 = chk.verify_tx_merkle_hashes(chain_id, chain)
            mchecked += mchecked1
            mbad += mbad1
        if ckstats:
            schecked1, sbad1 = chk.verify_block_stats(chain_id, blkstats)
            schecked += schecked1
            sbad += sbad1
    logger.info("All chains: %d blocks checked, %d bad merkles, %d bad blocks",
                max(mchecked, schecked), mbad, sbad)

    return sbad + mbad and 1

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
