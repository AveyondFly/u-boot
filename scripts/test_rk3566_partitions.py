#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""Run the real partition probes against MBR disks with a stale empty GPT."""
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
import zlib

from test_rk3566_boot_trace import actual_function

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    # Synthetic equivalent of the failing card: bootable FAT in MBR, valid
    # primary/backup GPT headers, and 128 entirely unused GPT entries.
    disk = bytearray(34 * 512 + 512)
    disk[510:512] = b'\x55\xaa'
    for index, (kind, start, size) in enumerate(((0x0c, 32768, 4194304),
                                               (0x83, 4227072, 15304179))):
        at = 446 + index * 16
        disk[at] = 0x80 if index == 0 else 0
        disk[at + 4] = kind
        struct.pack_into('<II', disk, at + 8, start, size)
    for offset, my_lba, alternate in ((512, 1, 122152959),
                                      (34 * 512, 122152959, 1)):
        header = bytearray(struct.pack('<8sIIIIQQQQ16sQIII', b'EFI PART',
            0x10000, 92, 0, 0, my_lba, alternate, 34, 122152926,
            bytes(range(16)), 2, 128, 128, zlib.crc32(bytes(16384))))
        struct.pack_into('<I', header, 16, zlib.crc32(header))
        disk[offset:offset + 92] = header
    return disk


class PartitionTests(unittest.TestCase):
    def run_probe(self, handheld):
        efi = (ROOT / 'disk/part_efi.c').read_text()
        dos = (ROOT / 'disk/part_dos.c').read_text()
        part = (ROOT / 'disk/part.c').read_text()
        header = '\n'.join(line for line in (ROOT / 'include/part_efi.h').read_text().splitlines()
                           if not line.startswith('#include'))
        pre = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned char u8;
typedef uint16_t u16, __le16;
typedef uint32_t u32, __le32;
typedef unsigned long long u64, __le64, lbaint_t;
typedef unsigned long ulong;
typedef struct { unsigned char b[16]; } efi_guid_t;
#define __packed __attribute__((packed))
#define CONFIG_ARCH_ROCKCHIP 1
#define CONFIG_RKIMG_BOOTLOADER 1
#define CONFIG_IS_ENABLED(option) 0
#define CONFIG_EFI_PARTITION_ENTRIES_NUMBERS 64
#define le16_to_cpu(x) (x)
#define le32_to_cpu(x) (x)
#define le64_to_cpu(x) (x)
#define LBAFU "%llu"
#define LBAF "%llx"
#define debug(...) ((void)0)
#define DOS_PART_DEFAULT_SECTOR 512
#define MAX_EXT_PARTS 256
#define SIG_TYPE_NONE 0
#define SIG_TYPE_MBR 1
#define SIG_TYPE_GUID 2
#define PART_TYPE_UNKNOWN 0
#define PART_TYPE_DOS 2
#define PART_TYPE_EFI 5
#define ARCH_DMA_MINALIGN 64
#define ALLOC_CACHE_ALIGN_BUFFER(type,name,size) type name[(size)/sizeof(type)]
#define ALLOC_CACHE_ALIGN_BUFFER_PAD(type,name,count,size) unsigned char name##_buf[size]; type *name=(type *)name##_buf
#define PAD_SIZE(count,align) (((count)+(align)-1)/(align)*(align))
#define BLOCK_CNT(count,desc) (((count)+(desc)->blksz-1)/(desc)->blksz)
static void *memalign(size_t align,size_t size) { (void)align; return malloc(size); }
static u32 get_unaligned_le32(const void *p) { u32 v; memcpy(&v,p,4); return v; }
static u32 efi_crc32(const void *p,u32 size) {
 const u8 *data=p; u32 crc=~0U;
 for(u32 i=0;i<size;i++) {
  crc^=data[i]; for(unsigned j=0;j<8;j++) crc=(crc>>1)^((crc&1)?0xedb88320U:0);
 }
 return ~crc;
}
'''
        middle = r'''
#include "disk/part_dos.h"
struct blk_desc { ulong blksz,rawblksz; lbaint_t lba,rawlba; int part_type,if_type,devnum,sig_type; u32 mbr_sig; efi_guid_t guid_sig; };
typedef struct { lbaint_t start,size; ulong blksz; char name[32],type[32]; int bootable; u8 sys_ind; } disk_partition_t;
static unsigned char disk[34*512+512];
static unsigned repairs, gpt_reads;
static ulong blk_dread(struct blk_desc *dev,lbaint_t lba,lbaint_t count,void *out) {
 (void)dev;
 if(lba==122152959 && count==1) { memcpy(out,disk+34*512,512); return 1; }
 assert(lba+count<=34);
 if(lba) gpt_reads++;
 memcpy(out,disk+lba*512,count*512); return count;
}
static void part_set_generic_name(struct blk_desc *dev,int part,char *name) {
 (void)dev; snprintf(name,32,"partition-%d",part);
}
static int part_efi_repair(struct blk_desc *dev,gpt_entry *entries,gpt_header *head,int primary,int backup) {
 (void)dev; (void)entries; (void)head; (void)primary; (void)backup; repairs++; return 0;
}
#define FACTORY_UNKNOWN_LBA (0xffffffff - 34)
'''
        functions = ''
        for signature in ('static int pmbr_part_valid(struct partition *part)\n{',
                          'static int is_pmbr_valid(legacy_mbr * mbr)\n{',
                          'static int validate_gpt_header(', 'static int validate_gpt_entries(',
                          'static gpt_entry *alloc_read_gpt_entries(struct blk_desc *dev_desc,\n\t\t\t\t\t gpt_header *pgpt_head)\n{',
                          'static int is_gpt_valid(struct blk_desc *dev_desc, u64 lba,\n\t\t\tgpt_header *pgpt_head, gpt_entry **pgpt_pte)\n{',
                          'static int part_test_efi('):
            functions += actual_function(efi, signature)
        for signature in ('static inline unsigned int le32_to_int(', 'static inline int is_extended(',
                          'static inline int is_bootable(', 'static int test_block_type(',
                          'static int part_test_dos(', 'static int part_get_info_extended(',
                          'int part_get_info_dos('):
            functions += actual_function(dos, signature)
        selector = r'''
struct part_driver { int part_type; int (*test)(struct blk_desc *); const char *name; };
static struct part_driver drivers[]={{PART_TYPE_EFI,part_test_efi,"EFI"},{PART_TYPE_DOS,part_test_dos,"DOS"}};
#define ll_entry_start(type,list) drivers
#define ll_entry_count(type,list) 2
static void blkcache_invalidate(int type,int devnum) { (void)type; (void)devnum; }
'''
        selector += actual_function(part, 'void part_init(')
        post = r'''
int main(int argc,char **argv) {
 assert(argc==2); FILE *f=fopen(argv[1],"rb"); assert(f);
 assert(fread(disk,1,sizeof(disk),f)==sizeof(disk)); fclose(f);
 struct blk_desc dev={.blksz=512,.lba=122152960};
 part_init(&dev);
#ifdef CONFIG_RK3566_HANDHELD_BOOT
 assert(dev.part_type==PART_TYPE_DOS && gpt_reads==0 && repairs==0);
#else
 /* Reproduce the previous failure: a completely empty GPT wins over MBR. */
 assert(dev.part_type==PART_TYPE_EFI && gpt_reads>0 && repairs==0);
#endif
 disk_partition_t info;
 assert(part_get_info_dos(&dev,1,&info)==0);
 assert(info.start==32768 && info.size==4194304 && info.bootable==1);
 assert(part_get_info_dos(&dev,2,&info)==0 && info.start==4227072);
 /* A real protective MBR still selects GPT (e.g. an eMMC Linux install). */
 memset(disk+446,0,64); disk[450]=0xee; disk[454]=1;
 part_init(&dev); assert(dev.part_type==PART_TYPE_EFI && repairs==0);
 /* Hybrid MBRs may advertise GPT in any of the four slots. */
 memset(disk+446,0,64); disk[446+3*16+4]=0xee; disk[446+3*16+8]=1;
 part_init(&dev); assert(dev.part_type==PART_TYPE_EFI && repairs==0);
 memset(disk+446,0,64); disk[450]=0xee; disk[454]=1;
#ifdef CONFIG_RK3566_HANDHELD_BOOT
 disk[454]=2; gpt_reads=0;
 assert(part_test_efi(&dev)!=0 && !gpt_reads && !repairs);
 disk[454]=1; disk[510]=0;
 assert(part_test_efi(&dev)!=0 && !gpt_reads && !repairs);
 assert(!is_pmbr_valid(NULL));
#endif
 return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='rk3566-partition-test-') as tmp:
            root = Path(tmp)
            source, binary, image = root / 'test.c', root / 'test', root / 'disk.bin'
            source.write_text(pre + header + middle + functions + selector + post)
            image.write_bytes(fixture())
            command = ['cc', '-std=gnu99', '-Wall', '-Wextra', '-Werror',
                       '-Wno-sign-compare', '-I', str(ROOT), str(source), '-o', str(binary)]
            if handheld:
                command.append('-DCONFIG_RK3566_HANDHELD_BOOT=1')
            subprocess.run(command, check=True)
            subprocess.run([str(binary), str(image)], check=True)

    def test_handheld_selects_mbr_without_reading_or_repairing_stale_gpt(self):
        self.run_probe(True)

    def test_other_rockchip_boards_keep_vendor_partition_policy(self):
        self.run_probe(False)


if __name__ == '__main__':
    unittest.main()
