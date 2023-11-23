import torch
from torch import nn
from .attention import MultiHeadAttention, ScaledDotProductAttention

def sinusoid_encoding_table(seq_len, d_model):
    pos = torch.arange(seq_len, dtype=torch.float32).unsqueeze(1)
    dim = torch.arange(d_model // 2, dtype=torch.float32).view(1, -1)
    sin = torch.sin(pos / 10000 ** (2 * dim / d_model))
    cos = torch.cos(pos / 10000 ** (2 * dim / d_model))

    out = torch.zeros((pos.shape[0], d_model))
    out[:, ::2] = sin
    out[:, 1::2] = cos
    return out

# 映射类，将O和T进行处理拼接
class Projector_attn(nn.Module):
    def __init__(self, f_obj, f_grid, f_tag, f_out, topk, drop_rate=0.3):
        super().__init__()

        # for objects O
        self.obj_mlp1 = nn.Sequential(  # 全连接层1，包含归一化层、线性连接层、dropout层
            nn.LayerNorm(f_obj), nn.Linear(f_obj, f_out - 1), nn.Dropout(p=drop_rate)
        )

        self.keys = ("whole", "four", "nine", "sixteen", "twentyfive")   # 4种剪裁的图片网格

        # for grids
        for k in self.keys:
            mlp1 = nn.Sequential(   # 全连接层1
                nn.LayerNorm(f_grid), nn.Linear(f_grid, f_out - 1), nn.Dropout(p=drop_rate)
            )
            setattr(self, f"grid_mlp_{k}", mlp1)

            if k == 'whole':
                num_embeddings = 1
            elif k =='four':
                num_embeddings = 4
            elif k == 'nine':
                num_embeddings = 9
            elif k == 'sixteen':
                num_embeddings = 16
            elif k == 'twentyfive':
                num_embeddings = 25
            else:
                raise KeyError
            
            # pos = nn.Embedding.from_pretrained(   # 位置编码
            #     sinusoid_encoding_table(num_embeddings, f_out), freeze=True
            # )
            # setattr(self, f"grid_pos_{k}", pos)

            pos_embed = nn.Parameter(torch.zeros(1, num_embeddings, f_out -1))  # 位置编码 # (1, 1, 512)
            pos_drop = nn.Dropout(p=drop_rate)

            nn.init.trunc_normal_(pos_embed, std=0.02)
            setattr(self, f"grid_pos_{k}", pos_embed)
            setattr(self, f"grid_pos_drop_{k}", pos_drop)

        # for tags
        for k in self.keys:
            mlp2 = nn.Sequential(
                nn.LayerNorm(f_tag), nn.Linear(f_tag, f_out -1), nn.Dropout(p=drop_rate)
            )
            setattr(self, f"tag_mlp_{k}", mlp2)


            if k == 'whole':
                num_embeddings = 1
            elif k =='four':
                num_embeddings = 4
            elif k == 'nine':
                num_embeddings = 9
            elif k == 'sixteen':
                num_embeddings = 16
            elif k == 'twentyfive':
                num_embeddings = 25
            else:
                raise KeyError
            
            # pos = nn.Embedding.from_pretrained(
            #     sinusoid_encoding_table(num_embeddings, f_out), freeze=True
            # )
            # setattr(self, f"tag_pos_{k}", pos)
            pos_embed = nn.Parameter(torch.zeros(1, num_embeddings * topk, f_out -1 ))  # 位置编码
            pos_drop = nn.Dropout(p=drop_rate)
            nn.init.trunc_normal_(pos_embed, std=0.02)
            setattr(self, f"tag_pos_{k}", pos_embed)
            setattr(self, f"tag_pos_drop_{k}", pos_drop)


        # for co-attention
        self.self_attn = MultiHeadAttention(d_model=512, d_k=64, d_v=64, h=8, dropout=.1,
                                        identity_map_reordering=False,
                                        attention_module=None,
                                        attention_module_kwargs=None)

        self.cross_attn = MultiHeadAttention(d_model=512, d_k=64, d_v=64, h=8, dropout=.1,
                                        identity_map_reordering=False,
                                        attention_module=None,
                                        attention_module_kwargs=None)
        # self.ffn = nn.Sequential(
        #     nn.LayerNorm(f_out), nn.Linear(f_out, f_out), nn.Dropout(p=drop_rate),
        # )
        self.ffn = nn.Sequential(
            nn.LayerNorm(f_out), nn.Linear(512,512), nn.ReLU(), nn.Dropout(p=drop_rate))



    def forward(self, obj, grid, tag):
        # img = vis_ctx[:, None, :]   # (b_s, _, 768)
        embed_obj = []
        embed_grid = []
        embed_tag = []

        # object O
        obj_mask = (torch.sum(torch.abs(obj), dim=-1) == 0)  # N x S
        obj_embed = self.obj_mlp1(obj) # -> (b_s, 1, f_out)
        obj_embed[obj_mask] = 0.
        # 模态0
        x = 0
        obj_embed_ = torch.cat((x * torch.ones((obj_embed.shape[0],obj_embed.shape[1], 1)).cuda(), obj_embed), dim=-1)  # (b_s, 1+f_out)
        embed_obj.append(obj_embed_)
        embed_obj_ = torch.cat(embed_obj, dim=1)  # (b_s, 50, 512)


        # grids G
        x = 1
        for k in self.keys:
            embed_k = grid[k]  # (b_s, 9*k, d)
            # pos_k = grid[k]["pos"]  # (b_s, 9*k, d)
            mlp1 = getattr(self, f"grid_mlp_{k}")  # -> (b_s, 9*k, f_out)
            pos_embed = getattr(self, f"grid_pos_{k}")
            pos_drop = getattr(self, f"grid_pos_drop_{k}")
            # embed_k = pos_drop(mlp1(embed_k) + pos_embed)    # 加上位置编码
            embed_k = mlp1(embed_k)
            embed_k = pos_drop(embed_k + pos_embed)
            embed_k = torch.cat((x * torch.ones((embed_k.shape[0],embed_k.shape[1], 1)).cuda(), embed_k), dim=-1)  # (b_s, 1+f_out) # 加上模态1

            embed_grid.append(embed_k)
        embed_grid_ = torch.cat(embed_grid, dim=1)  # (b_s, 1+4+9+16+25, 512)



        # tags T
        x = 2
        for k in self.keys:
            embed_k = tag[k]
            # pos_k = tag[k]["pos"]
            mlp2 = getattr(self, f"tag_mlp_{k}")
            # mlp_pos = getattr(self, f"tag_pos_{k}")
            pos_embed = getattr(self, f"tag_pos_{k}")
            pos_drop = getattr(self, f"tag_pos_drop_{k}")
            embed_k = mlp2(embed_k)
            embed_k = pos_drop(embed_k + pos_embed)
            embed_k = torch.cat((x * torch.ones((embed_k.shape[0],embed_k.shape[1], 1)).cuda(), embed_k), dim=-1)  # (b_s, 1+f_out)

            embed_tag.append(embed_k)
        embed_tag_ = torch.cat(embed_tag, dim=1)  # (b_s, (1+4+9+16+25)*k, 512)

        embed_tag_ = self.self_attn(embed_tag_, embed_tag_, embed_tag_) + embed_tag_
        embed_tag_ = self.cross_attn(embed_tag_, embed_grid_, embed_grid_) + embed_tag_
        embed_tag_ = self.ffn(embed_tag_) + embed_tag_

        return torch.cat((embed_obj_, embed_grid_, embed_tag_), dim=1)
        # 将obj、grid、tag进行拼接[o, g..., t...]
        # -> (b_s, 50+（1+9+16+25）*k , 512)
